import asyncio
import os
import requests
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
import uvicorn

app = FastAPI()

# -----------------------------------------------------------------------------
# CONFIGURAÇÕES DE APIS E NOTIFICAÇÕES
# -----------------------------------------------------------------------------
API_KEY = "87218213ea542ba95badcd5fe3d057c0"
BASE_URL = "https://v3.football.api-sports.io"

# TELEGRAM CONFIGURADO
TELEGRAM_BOT_TOKEN = "8833467196:AAGy5UY-wGyvF6hJEAXYUW-hSD3mskpWb7I"
TELEGRAM_CHAT_ID = "5384859613"

HEADERS = {
    'x-apisports-key': API_KEY
}

alertas_enviados_live = set()
alertas_enviados_pre = set()

def enviar_notificacao_telegram(mensagem: str):
    """Envia mensagem no seu celular via Telegram"""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": mensagem,
        "parse_mode": "HTML"
    }
    try:
        requests.post(url, json=payload, timeout=5)
    except Exception as e:
        print(f"Erro ao enviar notificação no Telegram: {e}")

class ConnectionManager:
    def __init__(self):
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)
        await websocket.send_json({
            "tipo": "LIVE",
            "texto": "Scanner API-Football Ativo! Monitorando ligas globais..."
        })

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast(self, message: dict):
        for connection in self.active_connections:
            try:
                await connection.send_json(message)
            except Exception as e:
                print(f"Erro no envio via WebSocket: {e}")

manager = ConnectionManager()

@app.get("/")
async def get():
    caminho_template = os.path.join(os.path.dirname(__file__), "templates", "index.html")
    with open(caminho_template, "r", encoding="utf-8") as f:
        html_content = f.read()
    return HTMLResponse(content=html_content)

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(websocket)

def obter_jogos_ao_vivo():
    url = f"{BASE_URL}/fixtures?live=all"
    try:
        res = requests.get(url, headers=HEADERS, timeout=10)
        dados = res.json()
        
        requests_left = res.headers.get("x-ratelimit-requests-remaining", "N/A")
        print(f"[API-FOOTBALL] Requisições restantes hoje: {requests_left}")
        
        return dados.get("response", [])
    except Exception as e:
        print(f"Erro ao buscar partidas ao vivo: {e}")
        return []

def obter_proximos_jogos():
    url = f"{BASE_URL}/fixtures?next=10"
    try:
        res = requests.get(url, headers=HEADERS, timeout=10)
        return res.json().get("response", [])
    except Exception as e:
        print(f"Erro ao buscar próximos jogos: {e}")
        return []

async def motor_de_analise():
    while True:
        print("\n[SCANNER API-FOOTBALL] Analisando métricas de partidas no mundo...")

        jogos_live = obter_jogos_ao_vivo()
        print(f"[LIVE] Partidas em andamento localizadas: {len(jogos_live)}")

        for jogo in jogos_live:
            fixture = jogo.get("fixture", {})
            fixture_id = fixture.get("id")
            minuto = fixture.get("status", {}).get("elapsed") or 1

            teams = jogo.get("teams", {})
            time_casa = teams.get("home", {}).get("name", "Casa")
            time_fora = teams.get("away", {}).get("name", "Fora")

            goals = jogo.get("goals", {})
            gols_casa = goals.get("home") or 0
            gols_fora = goals.get("away") or 0
            total_gols = gols_casa + gols_fora

            cantos = 0
            chutes_gol = 0
            chutes_fora = 0
            cartoes_amarelos = 0
            cartoes_vermelhos = 0

            events = jogo.get("events", [])
            for ev in events:
                if ev.get("type") == "Card":
                    if ev.get("detail") == "Yellow Card":
                        cartoes_amarelos += 1
                    elif ev.get("detail") == "Red Card":
                        cartoes_vermelhos += 1

            statistics = jogo.get("statistics", [])
            for team_stats in statistics:
                for stat in team_stats.get("statistics", []):
                    tipo = stat.get("type")
                    val = stat.get("value") or 0
                    
                    if tipo == "Corner Kicks":
                        cantos += val
                    elif tipo == "Shots on Goal":
                        chutes_gol += val
                    elif tipo == "Shots off Goal":
                        chutes_fora += val

            finalizacoes = chutes_gol + chutes_fora
            total_cartoes = cartoes_amarelos + cartoes_vermelhos

            # --- GATILHO 1: PRESSÃO DE GOLS ---
            ritmo_chutes = finalizacoes / minuto
            chave_gol = f"gol_{fixture_id}"

            if 15 <= minuto <= 75 and ritmo_chutes >= 0.15 and chave_gol not in alertas_enviados_live:
                alertas_enviados_live.add(chave_gol)
                linha_sugerida = total_gols + 0.5
                texto = f"⚽ <b>ALERTA DE GOLS IN-PLAY</b>\n\n<b>{time_casa} x {time_fora}</b>\nEntrada: Mais de {linha_sugerida} Gols\nMinuto: {minuto}'\nFinalizações: {finalizacoes}"
                
                await manager.broadcast({"tipo": "LIVE", "texto": texto})
                enviar_notificacao_telegram(texto)

            # --- GATILHO 2: PROJEÇÃO DE ESCANTEIOS ---
            taxa_cantos_min = cantos / minuto
            projecao_cantos = cantos + (taxa_cantos_min * (90 - minuto))
            chave_canto = f"canto_{fixture_id}"

            if 20 <= minuto <= 80 and projecao_cantos >= 8.5 and cantos >= 3 and chave_canto not in alertas_enviados_live:
                alertas_enviados_live.add(chave_canto)
                linha_canto = cantos + 2
                texto = f"🚩 <b>ALERTA DE ESCANTEIOS</b>\n\n<b>{time_casa} x {time_fora}</b>\nEntrada: Mais de {linha_canto} Escanteios\nMinuto: {minuto}'\nCantos Atuais: {cantos} (Projeção: {projecao_cantos:.1f})"
                
                await manager.broadcast({"tipo": "LIVE", "texto": texto})
                enviar_notificacao_telegram(texto)

            # --- GATILHO 3: CARTÕES ---
            chave_cartao = f"cartao_{fixture_id}"
            if 30 <= minuto <= 75 and total_cartoes >= 3 and chave_cartao not in alertas_enviados_live:
                alertas_enviados_live.add(chave_cartao)
                texto = f"🟨 <b>ALERTA DE CARTÕES</b>\n\n<b>{time_casa} x {time_fora}</b>\nEntrada: Mais de {total_cartoes + 1.5} Cartões\nMinuto: {minuto}'\nTotal de Cartões: {total_cartoes}"
                
                await manager.broadcast({"tipo": "LIVE", "texto": texto})
                enviar_notificacao_telegram(texto)

        await asyncio.sleep(900)

async def manter_vivo():
    """Faz requisições para a própria URL a cada 10 minutos para evitar que o Render adormeça."""
    await asyncio.sleep(10)
    url_propria = "https://meu-bot-futebol-636r.onrender.com/"
    while True:
        try:
            requests.get(url_propria, timeout=5)
            print("[KEEP-ALIVE] Ping enviado para manter o servidor ativo.")
        except Exception as e:
            print(f"[KEEP-ALIVE] Erro ao enviar ping: {e}")
        await asyncio.sleep(600)  # Executa a cada 10 minutos (600 segundos)

@app.on_event("startup")
async def startup_event():
    enviar_notificacao_telegram("🚀 <b>Scanner de Futebol Conectado!</b>\n\nNotificações via Telegram ativas e monitorando partidas.")
    asyncio.create_task(motor_de_analise())
    asyncio.create_task(manter_vivo())

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
