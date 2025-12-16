import redis
import datetime
import json
import random
import os
import time

# --- CONFIGURAÇÃO (AJUSTADO PARA O AMBIENTE K8s) ---
# Usar 'redis-service' como padrão, que é o nome do serviço no Kubernetes
REDIS_HOST = os.environ.get('REDIS_HOST', 'redis-service') 
r = redis.StrictRedis(host=REDIS_HOST, decode_responses=True)

# --- DADOS MOCK ---
ITENS = [
    "Laptop Gamer RGB X4000", "Smartphone ZFlip Pro", "Fone de Ouvido Noise-Cancelling",
    "Smartwatch Ultra 2", "Câmera Mirrorless Profissional", "Tablet XPTO 12\"",
    "Cadeira Gamer Ergonômica", "Monitor 4K 144Hz", "Console Portátil Retro",
    "Drone com Câmera HD", "Aspirador Robô Inteligente", "Kit de Ferramentas Premium",
    "Bicicleta Elétrica Dobrável", "Projetor 1080p Compacto", "Máquina de Café Espresso",
    "HD Externo 8TB", "Impressora 3D Hobby", "Placa de Vídeo RTX 5080",
    "Violão Eletroacústico", "Kit LEGO Edição Limitada"
]

USUARIOS = [
    {"id": 1, "nome": "Alice B."}, {"id": 2, "nome": "Bob C."},
    {"id": 3, "nome": "Charlie D."}, {"id": 4, "nome": "Diana E."},
    {"id": 5, "nome": "Ethan F."}, {"id": 6, "nome": "Fiona G."}
]

def seed_users():
    """Cria usuários base e garante que o contador de ID não seja menor."""
    r.set('next_user_id', len(USUARIOS))
    for user in USUARIOS:
        user_data = {
            "id": str(user["id"]),
            "nome": user["nome"],
            "email": f"{user['nome'].lower().replace(' ', '.').split('.')[0]}@{user['nome'].lower().replace(' ', '.').split('.')[1]}.com"
        }
        r.hset(f'user:{user["id"]}', mapping=user_data)
        
def get_next_id(key):
    """Incrementa um contador no Redis e retorna o novo ID."""
    return r.incr(f'next_{key}_id')

def seed_auctions(num_leiloes=20):
    """Cria um conjunto de leilões ativos e simula lances em parte deles."""
    print("Iniciando seed de dados...")
    
    seed_users()
    
    current_time = datetime.datetime.now()
    
    for i in range(1, num_leiloes + 1):
        auction_id = str(get_next_id('auction'))
        
        titulo = random.choice(ITENS)
        dono = random.choice(USUARIOS)
        
        preco_inicial = round(random.uniform(50.0, 2000.0), 2)
        minutos_restantes = random.randint(60, 2880) 
        tempo_restante_delta = datetime.timedelta(minutes=minutos_restantes)
        termino = current_time + tempo_restante_delta 
        
        # Leilão base
        leilao_data = {
            'id': auction_id,
            'titulo': titulo,
            'proprietario_id': str(dono['id']),
            'preco_inicial': preco_inicial,
            'lance_atual': preco_inicial,
            'usuario_atual_id': str(dono['id']),
            'horario_termino': termino.strftime('%Y-%m-%d %H:%M:%S'),
            'ativo': 'True'
        }
        
        # --- LÓGICA DE LANCE OBRIGATÓRIA (SIMULAÇÃO) ---
        num_lances = random.randint(2, 10) 
        lance_atual = preco_inicial
        usuario_atual = dono
        
        for _ in range(num_lances):
            participantes_validos = [u for u in USUARIOS if u['id'] != usuario_atual['id']]
            
            if not participantes_validos:
                break
            
            # Garante que o primeiro lance seja de um não-dono
            if _ == 0:
                 proximo_lance_user = random.choice([u for u in participantes_validos if u['id'] != dono['id']])
                 if proximo_lance_user is None:
                     proximo_lance_user = random.choice(participantes_validos)
            else:
                 proximo_lance_user = random.choice(participantes_validos)

            aumento = lance_atual * random.uniform(0.05, 0.20)
            novo_valor = round(lance_atual + aumento, 2)
            
            lance_atual = novo_valor
            usuario_atual = proximo_lance_user
            
            # Registra o lance no ZSET
            bid_data = json.dumps({'user_id': proximo_lance_user['id'], 'user_name': proximo_lance_user['nome'], 'valor': novo_valor, 'timestamp': datetime.datetime.now().isoformat()})
            r.zadd(f'bids:{auction_id}', {bid_data: novo_valor})

        # Atualiza o leilão com o estado final do último lance
        leilao_data['lance_atual'] = lance_atual
        leilao_data['usuario_atual_id'] = str(usuario_atual['id'])
        
        # Todos os valores precisam ser strings para o HSET
        leilao_str = {k: str(v) for k, v in leilao_data.items()}
        r.hset(f'auction:{auction_id}', mapping=leilao_str)
        r.sadd('active_auctions', auction_id)
        
    print(f"Seed concluído. {num_leiloes} leilões ativos criados, todos com lances simulados.", flush=True)

def check_and_seed():
    """Verifica se existem leilões ativos e faz o seed se o Redis estiver vazio."""
    # Garante que a conexão do Redis dentro da função use a configuração de ambiente
    r_check = redis.StrictRedis(host=REDIS_HOST, decode_responses=True)
    
    if not r_check.exists('next_auction_id'):
        seed_auctions()
        return True
    return False

if __name__ == '__main__':
    try:
        r.ping()
        print(f"Conectado ao Redis em {REDIS_HOST}.")
        check_and_seed()
    except redis.exceptions.ConnectionError as e:
        print(f"Erro ao conectar ao Redis: {e}")