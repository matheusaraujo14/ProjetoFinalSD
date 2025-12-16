import redis
import json
import time
import requests
import os

# --- CONFIGURAÇÃO ---

# Tenta ler do ambiente K8s
REDIS_HOST = os.environ.get('REDIS_HOST', 'redis-service') 
CANAL_EVENTOS = 'leiloes_finalizados'

# Este é o URL do Webhook do Discord que você configurou
DISCORD_WEBHOOK_URL = os.environ.get('DISCORD_WEBHOOK_URL', 'https://discord.com/api/webhooks/YOUR_WEBHOOK_ID/YOUR_WEBHOOK_TOKEN')

# --- FUNÇÕES AUXILIARES ---

def get_auction_details(auction_id):
    """Busca detalhes de um leilão FECHADO (closed:ID)."""
    try:
        r = redis.StrictRedis(host=REDIS_HOST, decode_responses=True)
        # O resultado final é armazenado na chave 'closed:ID'
        details = r.hgetall(f'closed:{auction_id}')
        if details:
            return details
        return None
    except Exception as e:
        print(f"ERRO ao buscar detalhes do Redis: {e}", flush=True)
        return None

def send_discord_notification(details):
    """Envia uma notificação formatada para o Discord via Webhook."""
    try:
        auction_id = details.get('id', 'N/A')
        titulo = details.get('titulo', 'N/A')
        status = details.get('status', 'N/A')
        valor_final = details.get('valor_final', 'N/A')
        
        if status == 'ENCERRADO':
            vencedor_nome = details.get('vencedor_nome', 'N/A')
            vencedor_email = details.get('vencedor_email', 'N/A')
            
            # Mensagem para o Discord
            embed_color = 3066993  # Verde
            message = f"🏆 **LEILÃO ENCERRADO: {titulo}**"
            fields = [
                {"name": "ID", "value": auction_id, "inline": True},
                {"name": "Status", "value": status, "inline": True},
                {"name": "Valor Final", "value": f"R$ {valor_final}", "inline": True},
                {"name": "Vencedor", "value": vencedor_nome, "inline": True},
                {"name": "Contato", "value": vencedor_email, "inline": False},
            ]
            
        elif status == 'CANCELADO':
            embed_color = 15158332 # Vermelho
            message = f"❌ **LEILÃO CANCELADO: {titulo}**"
            fields = [
                {"name": "ID", "value": auction_id, "inline": True},
                {"name": "Status", "value": status, "inline": True},
                {"name": "Preço Base", "value": f"R$ {valor_final}", "inline": True},
            ]
            
        else:
            return

        # Estrutura do Webhook do Discord
        payload = {
            "content": message,
            "embeds": [{
                "title": f"Resultado Final do Leilão #{auction_id}",
                "color": embed_color,
                "fields": fields,
                "timestamp": datetime.datetime.now().isoformat()
            }]
        }

        response = requests.post(
            DISCORD_WEBHOOK_URL, 
            json=payload,
            headers={'Content-Type': 'application/json'}
        )
        
        # Este é o ponto onde o ERRO HTTP APARECERÁ!
        response.raise_for_status() 
        
        print(f"✅ Notificação do Leilão {auction_id} enviada ao Discord!", flush=True)

        # 4. Envia notificação para o cliente web (usando RPUSH no Redis)
        r_notif = redis.StrictRedis(host=REDIS_HOST, decode_responses=True)
        vencedor_id = details.get('vencedor_id')
        
        if status == 'ENCERRADO' and vencedor_id and vencedor_id != 'N/A':
            r_notif.rpush(f'user_notif:{vencedor_id}', f"🏆 PARABÉNS! Você VENCEU o leilão '{titulo}' por R$ {valor_final}!")
        
    except requests.exceptions.HTTPError as e:
        print(f"ERRO ao enviar notificação para o Discord: {e}", flush=True)
        print(f"URL: {DISCORD_WEBHOOK_URL}", flush=True)
    except Exception as e:
        print(f"ERRO inesperado na notificação do Discord: {e}", flush=True)


def listen_for_events():
    """Loop principal que escuta eventos do Redis Pub/Sub."""
    
    # Usa conexão local para cada tentativa (garante estado limpo)
    try:
        global r, p
        r = redis.StrictRedis(host=REDIS_HOST, decode_responses=True)
        p = r.pubsub()
        p.subscribe(CANAL_EVENTOS)
        
        print(f"Agente de IA iniciado. Escutando canal: '{CANAL_EVENTOS}' no Redis em {REDIS_HOST}...", flush=True)
    except Exception as e:
        print(f"ERRO DE CONEXÃO INICIAL COM O REDIS: {e}", flush=True)
        # Tenta reconectar a cada 5 segundos
        time.sleep(5)
        listen_for_events()
        return

    while True:
        try:
            message = p.get_message()
            if message and message['type'] == 'message':
                data = json.loads(message['data'])
                auction_id = data.get('auction_id')
                status = data.get('status')
                
                print("\n--- NOVO EVENTO RECEBIDO ---", flush=True)
                print(f"Leilão ID: {auction_id}, Status: {status}", flush=True)
                
                # 1. Busca os detalhes finais do leilão (da chave closed:ID)
                details = get_auction_details(auction_id)
                
                if details:
                    print(f"Detalhes do Leilão {auction_id} recuperados.", flush=True)
                    
                    # 2. Envia a notificação
                    send_discord_notification(details)
                else:
                    print(f"AVISO: Não foi possível encontrar os detalhes do leilão fechado ID: {auction_id}", flush=True)

            time.sleep(0.1) 
        except Exception as e:
            print(f"ERRO no loop do Worker: {e}. Tentando reconectar...", flush=True)
            time.sleep(5)
            # Ao invés de tentar lidar com o erro aqui, reinicia a função de escuta
            listen_for_events()
            break

if __name__ == '__main__':
    listen_for_events()