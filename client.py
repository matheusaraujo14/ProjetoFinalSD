import requests
import os
import json
import time
import threading 
import redis 
import sys
import datetime

# --- CONFIGURAÇÃO ---
API_URL = "http://127.0.0.1:5000"

# Variáveis globais para o usuário logado e estado
USER_ID = None
NOME_USUARIO = None
SUBSCRIBED_AUCTIONS = set() 
PUBSUB_OBJECT = None        
IS_NOTIFYING = False        
ALERTA_LANCE = None # Variável para armazenar a mensagem de alerta persistente

# --- FUNÇÕES DE PUB/SUB ---

def pubsub_listener():
    """
    Thread de escuta com lógica de auto-reconexão do Redis e notificação.
    """
    global PUBSUB_OBJECT, IS_NOTIFYING, ALERTA_LANCE
    
    while True: # Loop externo para reconexão
        try:
            r_pubsub = redis.StrictRedis(host='127.0.0.1', port=6379, decode_responses=True)
            PUBSUB_OBJECT = r_pubsub.pubsub()
            
            if SUBSCRIBED_AUCTIONS:
                channels_to_resubscribe = [f'bid_updates:{id}' for id in SUBSCRIBED_AUCTIONS]
                PUBSUB_OBJECT.subscribe(*channels_to_resubscribe)

            # Loop interno de escuta
            while True: 
                mensagem = PUBSUB_OBJECT.parse_response(block=False, timeout=1) 
                
                if mensagem and isinstance(mensagem, list) and mensagem[0] == b'message':
                    
                    data = json.loads(mensagem[3])
                    
                    if int(data.get('user_id', 0)) != USER_ID:
                        
                        IS_NOTIFYING = True 
                        
                        # 1. Armazena a mensagem completa para ser exibida na próxima recarga
                        ALERTA_LANCE = (
                            f"🚨 UM NOVO LANCE FOI DADO NO LEILÃO {data['titulo']} (ID: {data['auction_id']}) EM QUE VOCÊ ESTÁ PARTICIPANDO.\n"
                            f"  > Novo Lance: R$ {data['valor']:.2f} por {data['usuario']}"
                        )
                        
                        # 2. Imprime a mensagem no console para 'quebrar' o input() bloqueado
                        print("\n" + "#"*70)
                        print(ALERTA_LANCE)
                        print("\n--- Pressione ENTER para recarregar e ver o novo status ---")
                        print("#"*70 + "\n")
                    
                time.sleep(0.1) 
        
        except redis.exceptions.ConnectionError:
            print(f"\n🔴 [PUB/SUB ERRO] Conexão com o Redis perdida. Tentando reconectar em 5s...")
            PUBSUB_OBJECT = None 
            time.sleep(5) 
        
        except Exception:
            time.sleep(1)


def start_pubsub_thread():
    """Inicia a thread de escuta."""
    listener_thread = threading.Thread(target=pubsub_listener, daemon=True)
    listener_thread.start()

# --- FUNÇÕES DA INTERFACE ---

def limpar_tela():
    os.system('cls' if os.name == 'nt' else 'clear')

def registrar_usuario():
    while True:
        nome = input("Digite seu nome de usuário: ")
        if not nome: continue
        try:
            response = requests.post(f"{API_URL}/register", json={'nome': nome})
            if response.status_code == 201:
                data = response.json()
                return data['user_id'], data['nome']
            else:
                print(f"Erro ao registrar: {response.json().get('erro', 'Erro desconhecido')}")
        except requests.exceptions.ConnectionError:
            print("Erro de conexão. O servidor Flask está rodando na porta 5000?")
            time.sleep(2)
        return None, None

def mostrar_notificacoes(user_id):
    try:
        response = requests.get(f"{API_URL}/user/{user_id}/notifications")
        if response.status_code == 200:
            notificacoes = response.json()
            if notificacoes:
                print("\n" + "*"*50)
                for msg in notificacoes:
                    print(f"🔔 NOTIFICAÇÃO DE VITÓRIA: {msg}")
                print("*"*50 + "\n")
    except requests.exceptions.RequestException: pass

def mostrar_status(user_id):
    global IS_NOTIFYING, ALERTA_LANCE
    
    # 1. Sempre limpa a tela para recarregar o menu
    limpar_tela() 
    
    # 2. Exibe o alerta persistente se houver (o resultado do lance recebido)
    if ALERTA_LANCE:
        print("\n" + "!"*70)
        print("🔔 NOTIFICAÇÃO DE LANCE RECEBIDA")
        print(ALERTA_LANCE)
        print("!"*70 + "\n")
        # Limpa o alerta para que não apareça na próxima recarga
        ALERTA_LANCE = None 
    
    # Exibe notificações de vitória
    mostrar_notificacoes(user_id) 
    
    print("\n" + "="*50)
    print(f"USUÁRIO: {NOME_USUARIO} (ID: {user_id})")
    print("--- LEILÕES ATIVOS ---")
    
    try:
        response = requests.get(f"{API_URL}/auction/status")
        if response.status_code == 200:
            leiloes_ativos = response.json()
            if not leiloes_ativos:
                print("Nenhum leilão ativo no momento. Crie um!")
            else:
                for leilao in leiloes_ativos:
                    owner_tag = "(Seu Leilão)" if int(leilao["proprietario_id"]) == user_id else ""
                    print(f"ID: {leilao['id']} | TÍTULO: {leilao['titulo']} {owner_tag}")
                    
                    print(f"  > Lance Atual: R$ {leilao['lance_atual']:.2f} por {leilao['usuario_atual']}")
                    print(f"  > Tempo Restante: {leilao['tempo_restante']}")
        else:
            print(f"Erro ao obter status: {response.json().get('erro', 'Erro de API')}")
    except requests.exceptions.ConnectionError:
        print("🔴 ERRO DE CONEXÃO: O servidor Flask não está acessível.")
        
    print("="*50)

def criar_leilao(user_id):
    try:
        titulo = input("Título do novo leilão: ")
        preco_inicial = float(input("Preço inicial: R$ "))
        duracao = int(input("Duração (em minutos): "))
        response = requests.post(f"{API_URL}/auction/create", json={
            'user_id': user_id, 'titulo': titulo, 'preco_inicial': preco_inicial, 'duracao_minutos': duracao
        })
        data = response.json()
        if response.status_code == 201:
            print(f"\n>> Leilão criado com SUCESSO! ID: {data['auction_id']}")
        else:
            print(f"\n>> ERRO: {data.get('erro', 'Erro desconhecido')}")
    except ValueError:
        print("Por favor, digite valores numéricos válidos para preço/duração.")
    except requests.exceptions.RequestException as e:
        print(f"Erro de comunicação com o servidor: {e}")
    input("\nPressione Enter para continuar...")

def mostrar_historico():
    limpar_tela()
    print("\n--- HISTÓRICO GERAL DE LEILÕES ENCERRADOS ---")
    try:
        response = requests.get(f"{API_URL}/auction/history")
        if response.status_code == 200:
            historico = response.json()
            if not historico:
                print("Nenhum leilão encerrado ainda.")
            else:
                for resultado in historico:
                    print(f"  > ITEM: {resultado['item']} | VENCEDOR: {resultado['usuario']} | PREÇO FINAL: R$ {resultado['preco']:.2f}")
        else:
            print(f"Erro ao consultar histórico: {response.json().get('erro', 'Erro de API')}")
    except requests.exceptions.RequestException as e:
        print(f"Erro de comunicação com o servidor: {e}")
    input("\nPressione Enter para continuar...")


def fazer_lance(user_id):
    global SUBSCRIBED_AUCTIONS, PUBSUB_OBJECT

    try:
        auction_id = input("Digite o ID do Leilão para o lance: ")
        valor = float(input("Digite o valor do seu lance: R$ "))
        
        response = requests.post(f"{API_URL}/auction/bid", json={
            'user_id': user_id,
            'auction_id': auction_id,
            'valor': valor
        })
        
        data = response.json()
        if response.status_code == 200:
            print(f"\n>> SUCESSO: {data['mensagem']}")
            
            # --- Lógica de Inscrição Específica ---
            channel_name = f'bid_updates:{auction_id}'
            
            if auction_id not in SUBSCRIBED_AUCTIONS:
                if PUBSUB_OBJECT:
                    print(f"--> Inscrevendo no canal: {channel_name}...")
                    PUBSUB_OBJECT.subscribe(channel_name) 
                    SUBSCRIBED_AUCTIONS.add(auction_id)
                else:
                    print("--> Aviso: Pub/Sub Listener ainda não está pronto. Tente refazer o lance em 5s.")

        else:
            print(f"\n>> ERRO: {data.get('erro', 'Erro desconhecido')}")
            
    except ValueError:
        print("Por favor, digite IDs e valores numéricos válidos.")
    except requests.exceptions.RequestException as e:
        print(f"Erro de comunicação com o servidor: {e}")
    
    input("\nPressione Enter para continuar...")


# --- INÍCIO DA EXECUÇÃO ---
if __name__ == '__main__':
    USER_ID, NOME_USUARIO = registrar_usuario()
    
    if USER_ID is None:
        sys.exit()

    start_pubsub_thread()

    while True:
        mostrar_status(USER_ID)
        
        print("Opções:")
        print("1. Atualizar lista de leilões")
        print("2. Fazer um lance em um item")
        print("3. Criar um novo leilão")
        print("4. Ver HISTÓRICO GERAL de leilões encerrados")
        print("5. Sair")
        
        escolha = input("Escolha: ")

        # Lógica de atualização forçada: se o usuário pressionou ENTER (string vazia) 
        # logo após receber uma notificação, o loop recarrega a tela.
        if IS_NOTIFYING:
            IS_NOTIFYING = False
            if not escolha.strip():
                continue 
        
        if escolha == "1":
            continue
        elif escolha == "2":
            fazer_lance(USER_ID)
        elif escolha == "3":
            criar_leilao(USER_ID)
        elif escolha == "4":
            mostrar_historico()
        elif escolha == "5":
            print("Saindo...")
            break
        else:
            print("Opção inválida.")
            input("Pressione Enter para continuar...")