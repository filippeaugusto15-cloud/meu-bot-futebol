import asyncio
import requests
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
import uvicorn

app = FastAPI()

# -----------------------------------------------------------------------------
# SUA CHAVE CONFIGURADA DA API-FOOTBALL
# -----------------------------------------------------------------------------
API_KEY = "87218213ea542ba95badcd5fe3d057c0"
BASE_URL = "https://v3.football.api-sports.io"

HEADERS = {
    'x-apisports-key': API_KEY
}

alertas_enviados_live = set()
alertas_enviados_pre = set()

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
    with open("templates/index.html", "r", encoding="utf-8") as f:
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


# --- REQUISIÇÕES DA API-FOOTBALL ---

def obter_jogos_ao_vivo():
    # Retorna todos os jogos em andamento no mundo com estatísticas no mesmo payload
    url = f"{BASE_URL}/fixtures?live=all"
    try:
        res = requests.get(url, headers=HEADERS, timeout=10)
        dados = res.json()
        
        # Monitora limite de requisições da cota gratuita
        requests_left = res.headers.get("x-ratelimit-requests-remaining", "N/A")
        print(f"[API-FOOTBALL] Requisições restantes hoje: {requests_left}")
        
        return dados.get("response", [])
    except Exception as e:
        print(f"Erro ao buscar partidas ao vivo: {e}")
        return []

def obter_proximos_jogos():
    # Busca os próximos 10 jogos agendados
    url = f"{BASE_URL}/fixtures?next=10"
    try:
        res = requests.get(url, headers=HEADERS, timeout=10)
        return res.json().get("response", [])
    except Exception as e:
        print(f"Erro ao buscar próximos jogos: {e}")
        return []


# --- MOTOR DE ANÁLISE ESTATÍSTICA ---

async def motor_de_analise():
    while True:
        print("\n[SCANNER API-FOOTBALL] Analisando métricas de partidas no mundo...")

        # =========================================================================
        # 1. ANÁLISE AO VIVO (IN-PLAY)
        # =========================================================================
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

            # Extração detalhada de estatísticas da API-Football
            cantos = 0
            chutes_gol = 0
            chutes_fora = 0
            cartoes_amarelos = 0
            cartoes_vermelhos = 0

            # Cartões rápidos
            events = jogo.get("events", [])
            for ev in events:
                if ev.get("type") == "Card":
                    if ev.get("detail") == "Yellow Card":
                        cartoes_amarelos += 1
                    elif ev.get("detail") == "Red Card":
                        cartoes_vermelhos += 1

            # Estatísticas de chutes e escanteios
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

            # --- GATILHO 1: PRESSÃO DE GOLS (Mínimo 0.15 chutes/min) ---
            ritmo_chutes = finalizacoes / minuto
            chave_gol = f"gol_{fixture_id}"

            if 15 <= minuto <= 75 and ritmo_chutes >= 0.15 and chave_gol not in alertas_enviados_live:
                alertas_enviados_live.add(chave_gol)
                linha_sugerida = total_gols + 0.5
                await manager.broadcast({
                    "tipo": "LIVE",
                    "texto": f"Entrada mais de {linha_sugerida} gols live {time_casa} x {time_fora} ({finalizacoes} finalizações em {minuto}')"
                })

            # --- GATILHO 2: PROJEÇÃO LINEAR DE ESCANTEIOS ---
            taxa_cantos_min = cantos / minuto
            projecao_cantos = cantos + (taxa_cantos_min * (90 - minuto))
            chave_canto = f"canto_{fixture_id}"

            if 20 <= minuto <= 80 and projecao_cantos >= 8.5 and cantos >= 3 and chave_canto not in alertas_enviados_live:
                alertas_enviados_live.add(chave_canto)
                linha_canto = cantos + 2
                await manager.broadcast({
                    "tipo": "LIVE",
                    "texto": f"Entrada mais de {linha_canto} escanteios live {time_casa} x {time_fora} (Projeção: {projecao_cantos:.1f} cantos)"
                })

            # --- GATILHO 3: JOGO QUENTE DE CARTÕES ---
            chave_cartao = f"cartao_{fixture_id}"
            if 30 <= minuto <= 75 and total_cartoes >= 3 and chave_cartao not in alertas_enviados_live:
                alertas_enviados_live.add(chave_cartao)
                await manager.broadcast({
                    "tipo": "LIVE",
                    "texto": f"Entrada mais de {total_cartoes + 1.5} cartões live {time_casa} x {time_fora} ({total_cartoes} cartões acumulados)"
                })

        # =========================================================================
        # 2. AGENDA DE PRÓXIMOS JOGOS (PRÉ-JOGO)
        # =========================================================================
        jogos_pre = obter_proximos_jogos()

        for jogo in jogos_pre[:5]:
            fixture = jogo.get("fixture", {})
            fixture_id = fixture.get("id")

            if fixture_id not in alertas_enviados_pre:
                teams = jogo.get("teams", {})
                time_casa = teams.get("home", {}).get("name", "Casa")
                time_fora = teams.get("away", {}).get("name", "Fora")
                liga = jogo.get("league", {}).get("name", "Liga")

                alertas_enviados_pre.add(fixture_id)

                await manager.broadcast({
                    "tipo": "PRE_LIVE",
                    "jogo": f"{time_casa} x {time_fora} ({liga})",
                    "entradas": [
                        "Jogo sob radar de pressão In-Play",
                        "Mercados monitorados: Gols, Escanteios e Cartões"
                    ]
                })

        # Intervalo de 180 segundos para economizar a cota gratuita
        await asyncio.sleep(180)

@app.on_event("startup")
async def startup_event():
    asyncio.create_task(motor_de_analise())

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)