from flask import Flask, request, jsonify
from flask_cors import CORS
import redis
import datetime
import json
import time
import os
from seed import check_and_seed # <-- AGORA ATIVA E IMPORTANDO

# --- CONFIGURAÇÃO ---
app = Flask(__name__)
CORS(app)

# Tenta ler do ambiente K8s, fallback para redis-service
REDIS_HOST = os.environ.get('REDIS_HOST', 'redis-service') 
r = redis.StrictRedis(host=REDIS_HOST, decode_responses=True)

CANAL_EVENTOS = 'leiloes_finalizados' 

# --- FUNÇÕES AUXILIARES ---

def get_next_id(key):
    """Incrementa um contador no Redis e retorna o novo ID."""
    return r.incr(f'next_{key}_id')

def get_user_data(user_id):
    """Busca dados completos do usuário (nome, e-mail)."""
    if not user_id:
        return {"nome": "N/A", "email": "N/A"}
    data = r.hgetall(f'user:{user_id}')
    return {
        "nome": data.get('nome', 'N/A'),
        "email": data.get('email', 'N/A'),
        "id": data.get('id', str(user_id))
    }

def check_and_close_auction(auction_id):
    """
    Verifica o tempo de um leilão. Se encerrado, move-o para o histórico
    e publica um evento no canal.
    """
    auction_id = str(auction_id)
    leilao = r.hgetall(f'auction:{auction_id}')
    
    if not leilao or leilao.get('ativo') == 'False':
        r.srem('active_auctions', auction_id)
        return False, "Leilão não ativo/inexistente."
    
    if 'horario_termino' not in leilao:
        print(f"ERRO: Leilão {auction_id} sem horário de término.", flush=True)
        r.srem('active_auctions', auction_id) 
        return True, "Dados incompletos e removido."
    
    # 1. Checa o horário
    termino = datetime.datetime.strptime(leilao['horario_termino'], '%Y-%m-%d %H:%M:%S')
    
    if datetime.datetime.now() > termino:
        # 2. Fecha o leilão no Redis
        r.hset(f'auction:{auction_id}', 'ativo', 'False')
        r.srem('active_auctions', auction_id)
        
        try:
            lance_atual = float(leilao.get('lance_atual', 0))
            preco_inicial = float(leilao.get('preco_inicial', 0))
        except ValueError:
            lance_atual = 0
            preco_inicial = 0
            
        resultado = {
            "id": auction_id,
            "titulo": leilao.get('titulo', 'N/A'),
            "proprietario_id": leilao.get('proprietario_id', 'N/A')
        }

        # 3. Define o status final
        if lance_atual <= preco_inicial:
            resultado["status"] = "CANCELADO"
            resultado["vencedor_id"] = "N/A"
            resultado["valor_final"] = preco_inicial
        else:
            resultado["status"] = "ENCERRADO"
            vencedor_id = leilao.get('usuario_atual_id')
            vencedor_data = get_user_data(vencedor_id)
            
            resultado["vencedor_id"] = vencedor_id if vencedor_id else 'N/A'
            resultado["vencedor_nome"] = vencedor_data['nome']
            resultado["vencedor_email"] = vencedor_data['email'] 
            resultado["valor_final"] = lance_atual
        
        resultado_str = {k: str(v) for k, v in resultado.items()}
        
        try:
            # Persiste os resultados finais (Chave closed:ID)
            r.hset(f'closed:{auction_id}', mapping=resultado_str)
            
            # === CORREÇÃO: PONTO CRÍTICO: PUBLICAÇÃO DO EVENTO ===
            # Usa uma nova conexão para evitar problemas de estado de Pub/Sub
            r_pub = redis.StrictRedis(host=REDIS_HOST, decode_responses=True)

            r_pub.publish(CANAL_EVENTOS, json.dumps({
                "auction_id": auction_id,
                "status": resultado["status"]
            }))
            
            # LOG VISÍVEL DE SUCESSO
            print("="*60, flush=True)
            print(f"| ✅ EVENTO PUBLICADO NO CANAL '{CANAL_EVENTOS.upper()}' |", flush=True)
            print(f"| Leilão ID: {auction_id} | Status: {resultado['status']} |", flush=True)
            print("="*60, flush=True)
            
            return True, resultado["status"]
            
        except Exception as e:
            print(f"ERRO ao fechar leilão {auction_id}: {e}", flush=True)
            return False, f"Erro inesperado: {e}"
    
    return False, "Leilão ainda ativo."

# --- ROTAS (Permanecem inalteradas) ---

@app.route('/register', methods=['POST'])
def register():
    """Registra um novo usuário no Redis."""
    data = request.json
    nome = data.get('nome')
    if not nome:
        return jsonify({"erro": "Nome é obrigatório"}), 400
        
    user_id = str(get_next_id('user'))
    
    r.hset(f'user:{user_id}', mapping={
        "id": user_id,
        "nome": nome,
        "email": f"{nome.lower().replace(' ', '.')}@sd.com"
    })
    
    return jsonify({"user_id": user_id, "nome": nome}), 201

@app.route('/auction/create', methods=['POST'])
def create_auction():
    """Cria um novo leilão."""
    data = request.json
    user_id = str(data.get('user_id'))
    titulo = data.get('titulo')
    preco_inicial = float(data.get('preco_inicial', 0))
    duracao_minutos = int(data.get('duracao_minutos', 5))

    if not user_id or not titulo or preco_inicial <= 0 or duracao_minutos <= 0:
        return jsonify({"erro": "Dados inválidos."}), 400

    auction_id = str(get_next_id('auction'))
    
    agora = datetime.datetime.now()
    termino = agora + datetime.timedelta(minutes=duracao_minutos)
    
    leilao_data = {
        "id": auction_id,
        "titulo": titulo,
        "proprietario_id": user_id,
        "preco_inicial": preco_inicial,
        "lance_atual": preco_inicial, # Inicialmente, o lance atual é o preço inicial
        "usuario_atual_id": "",
        "horario_termino": termino.strftime('%Y-%m-%d %H:%M:%S'),
        "ativo": "True"
    }

    r.hset(f'auction:{auction_id}', mapping={k: str(v) for k, v in leilao_data.items()})
    r.sadd('active_auctions', auction_id)
    
    return jsonify({"auction_id": auction_id, "status": "Criado"}), 201

@app.route('/auction/bid', methods=['POST'])
def place_bid():
    """Permite que um usuário dê um lance."""
    data = request.json
    user_id = str(data.get('user_id'))
    auction_id = str(data.get('auction_id'))
    valor = float(data.get('valor', 0))
    
    if valor <= 0 or not user_id or not auction_id:
        return jsonify({"erro": "Dados inválidos."}), 400

    leilao = r.hgetall(f'auction:{auction_id}')
    
    if not leilao or leilao.get('ativo') == 'False':
        return jsonify({"erro": "Leilão não encontrado ou já encerrado."}), 404
        
    lance_atual = float(leilao.get('lance_atual', 0))
    
    if valor <= lance_atual:
        return jsonify({"erro": f"O lance deve ser maior que o lance atual (R$ {lance_atual:.2f})."}), 400
        
    if leilao.get('proprietario_id') == user_id:
         return jsonify({"erro": "Você não pode dar lances no seu próprio leilão."}), 400

    # Pipeline para garantir atomicidade da atualização do lance
    pipe = r.pipeline()
    
    # Atualiza o leilão
    pipe.hset(f'auction:{auction_id}', mapping={
        'lance_atual': str(valor),
        'usuario_atual_id': user_id
    })
    
    # Adiciona o lance ao histórico (Sorted Set, ordenado pelo valor)
    timestamp = datetime.datetime.now().isoformat()
    user_data = get_user_data(user_id)
    bid_data = {
        "user_id": user_id,
        "user_name": user_data['nome'],
        "valor": valor,
        "timestamp": timestamp
    }
    
    # score=valor para ordenação; member=JSON string do lance
    pipe.zadd(f'bids:{auction_id}', {json.dumps(bid_data): valor}) 
    
    pipe.execute()
    
    # Publica evento do novo lance
    r.publish(f'bid_updates:{auction_id}', json.dumps(bid_data))

    return jsonify({"mensagem": "Lance registrado.", "novo_lance": valor}), 200

@app.route('/auction/<int:auction_id>/bids', methods=['GET'])
def get_auction_bids(auction_id):
    """Retorna todos os lances de um leilão, ordenados pelo valor (decrescente)."""
    bids = r.zrevrange(f'bids:{auction_id}', 0, -1)
    bid_list = [json.loads(bid) for bid in bids]
    return jsonify(bid_list), 200

@app.route('/auction/status', methods=['GET'])
def get_all_status():
    """Retorna o status de todos os leilões ativos e fecha os expirados."""
    active_ids = r.smembers('active_auctions')
    status_list = []
    
    agora = datetime.datetime.now()
    print(f"DEBUG FLASK: Verificando leilões às: {agora.isoformat()} (Total: {len(active_ids)})", flush=True)
    
    for auction_id in list(active_ids):
        # check_and_close_auction fecha, remove do set e publica o evento.
        fechado, status = check_and_close_auction(auction_id)
        
        if fechado:
            continue

        leilao = r.hgetall(f'auction:{auction_id}')
        if not leilao:
            continue
            
        try:
            termino = datetime.datetime.strptime(leilao['horario_termino'], '%Y-%m-%d %H:%M:%S')
            tempo_restante = termino - agora
            
            if tempo_restante.total_seconds() > 0:
                minutos = int(tempo_restante.total_seconds() // 60)
                segundos = int(tempo_restante.total_seconds() % 60)
                tempo_str = f"{minutos}m {segundos}s"
            else:
                tempo_str = "0m 0s (EXPIRADO - AGUARDANDO FECHAMENTO)"

            usuario_atual_id = leilao.get('usuario_atual_id')
            usuario_atual = get_user_data(usuario_atual_id).get('nome', 'Nenhum')

            status_list.append({
                "id": int(auction_id),
                "titulo": leilao['titulo'],
                "proprietario_id": leilao['proprietario_id'],
                "preco_inicial": float(leilao['preco_inicial']),
                "lance_atual": float(leilao['lance_atual']),
                "usuario_atual_id": usuario_atual_id,
                "usuario_atual": usuario_atual,
                "horario_termino": leilao['horario_termino'],
                "tempo_restante": tempo_str
            })
        except Exception as e:
             print(f"ERRO ao processar status do leilão {auction_id}: {e}", flush=True)

    return jsonify(status_list), 200

@app.route('/auction/history', methods=['GET'])
def get_history():
    """Retorna o histórico de leilões encerrados."""
    closed_ids = r.keys('closed:*')
    history_list = []
    
    for key in closed_ids:
        auction_id = key.split(':')[1]
        data = r.hgetall(key)
        
        vencedor_nome = data.get('vencedor_nome', 'N/A')
        
        history_list.append({
            "id": int(auction_id),
            "item": data.get('titulo', 'N/A'),
            "descricao": f"Vencedor: {vencedor_nome}, Valor: R$ {data.get('valor_final', '0.0')}",
            "status_final": data.get('status', 'N/A')
        })
        
    return jsonify(history_list), 200

@app.route('/user/<int:user_id>/notifications', methods=['GET'])
def check_vitoria_endpoint(user_id):
    """Verifica e consome notificações de vitória do Redis para o cliente web."""
    # O Worker de IA envia notificações de vitória/derrota para 'user_notif:ID'
    
    notificacoes = r.lrange(f'user_notif:{user_id}', 0, -1)
    
    # Consome as mensagens (limpa a lista)
    if notificacoes:
        r.ltrim(f'user_notif:{user_id}', len(notificacoes), -1)
        
    return jsonify(notificacoes), 200


if __name__ == '__main__':
    # 🎯 EXECUÇÃO DOS DADOS INICIAIS
    try:
        if check_and_seed():
            print("✅ Dados iniciais carregados.", flush=True)
        else:
            print("⚠️ Seed pulado: Dados já existentes no Redis.", flush=True)
    except Exception as e:
        print(f"ATENÇÃO: Falha ao executar o seed: {e}. O sistema continuará.", flush=True)

    app.run(host='0.0.0.0', port=5000)