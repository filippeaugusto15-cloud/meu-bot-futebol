import asyncio
import os
import requests
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
import uvicorn

app = FastAPI()

# -----------------------------------------------------------------------------
# CONFIGURAÇÕES DE APIS E NOTIFICAÇÕES (PLANO PRO)
# -----------------------------------------------------------------------------
API_KEY = "87218213ea542ba95badcd5fe3d057c0"
BASE_URL = "https://v3.football.api-sports.io"

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
            "texto": "Scanner API-Football (PRO) Ativo! Monitorando em tempo real..."
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
        print(f"[API-FOOTBALL PRO] Requisições restantes hoje: {requests_left}")
        
        return dados.get("response", [])
    except Exception as e:
        print(f"Erro ao buscar partidas ao vivo: {e}")
        return []

def obter_proximos_jogos():
    url = f"{BASE_URL}/fixtures?next=15"
    try:
        res = requests.get(url, headers=HEADERS, timeout=10)
        return res.json().get("response", [])
    except Exception as e:
        print(f"Erro ao buscar próximos jogos: {e}")
        return []

def obter_historico_time(team_id):
    """Busca histórico dos últimos 5 jogos de uma equipe para extrair gols e escanteios."""
    url = f"{BASE_URL}/fixtures?team={team_id}&last=5"
    try:
        res = requests.get(url, headers=HEADERS, timeout=10)
        jogos = res.json().get("response", [])
        
        total_gols = 0
        total_jogos = len(jogos)
        
        for j in jogos:
            g_casa = j.get("goals", {}).get("home") or 0
            g_fora = j.get("goals", {}).get("away") or 0
            total_gols += (g_casa + g_fora)
            
        media_gols = (total_gols / total_jogos) if total_jogos > 0 else 0
        return {"media_gols": media_gols, "qtd_jogos": total_jogos}
    except Exception as e:
        print(f"Erro ao buscar histórico do time {team_id}: {e}")
        return {"media_gols": 0, "qtd_jogos": 0}

# -----------------------------------------------------------------------------
# ANÁLISE PRÉ-JOGO
# -----------------------------------------------------------------------------
async def analisar_pre_jogo():
    print("\n[PRÉ-JOGO PRO] Analisando próximos jogos...")
    proximos = obter_proximos_jogos()

    for jogo in proximos:
        fixture = jogo.get("fixture", {})
        fixture_id = fixture.get("id")

        if fixture_id in alertas_enviados_pre:
            continue

        teams = jogo.get("teams", {})
        id_casa = teams.get("home", {}).get("id")
        nome_casa = teams.get("home", {}).get("name", "Casa")

        id_fora = teams.get("away", {}).get("id")
        nome_fora = teams.get("away", {}).get("name", "Fora")

        data_hora = fixture.get("date", "")[11:16] # HH:MM

        stats_casa = obter_historico_time(id_casa)
        stats_fora = obter_historico_time(id_fora)

        if stats_casa["qtd_jogos"] == 0 or stats_fora["qtd_jogos"] == 0:
            continue

        media_geral_gols = (stats_casa["media_gols"] + stats_fora["media_gols"]) / 2

        # Filtro de valor para envio
        if media_geral_gols >= 2.0:
            alertas_enviados_pre.add(fixture_id)

            sugestoes = []
            sugestoes.append("• Mais de 1.5 Gols na partida")

            if media_geral_gols >= 2.8:
                sugestoes.append("• Mais de 0.5 Gols no 1º Tempo")
                sugestoes.append("• Mais de 2.5 Escanteios no 1º Tempo")
            else:
                sugestoes.append("• Mais de 2.5 Cartões na partida")

            lista_sugestoes = "\n".join(sugestoes)

            texto = (
                f"📅 <b>ALERTA PRÉ-JOGO PRO</b>\n\n"
                f"⚽ <b>{nome_casa} x {nome_fora}</b>\n"
                f"⏰ Horário: {data_hora}\n"
                f"📊 Média Recente de Gols: <b>{media_geral_gols:.1f}</b>/jogo\n\n"
                f"💡 <b>Sugestão de Criar Aposta (Betano):</b>\n{lista_sugestoes}"
            )

            await manager.broadcast({"tipo": "PRE", "texto": texto})
            enviar_notificacao_telegram(texto)

# -----------------------------------------------------------------------------
# MOTOR PRINCIPAL (AO VIVO VELOZ)
# -----------------------------------------------------------------------------
async def motor_de_analise():
    contador_pre = 0
    while True:
        print("\n[SCANNER PRO] Varrendo partidas ao vivo...")

        # Roda pré-jogo a cada 30 ciclos (30 * 30s = 15 minutos)
        if contador_pre % 30 == 0:
            await analisar_pre_jogo()
        contador_pre += 1

        jogos_live = obter_jogos_ao_vivo()
        print(f"[LIVE] Partidas ativas no momento: {len(jogos_live)}")

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

            if 15 <= minuto <= 75 and ritmo_chutes >= 0.14 and chave_gol not in alertas_enviados_live:
                alertas_enviados_live.add(chave_gol)
                linha_sugerida = total_gols + 0.5
                texto = (
                    f"⚽ <b>ALERTA DE GOLS IN-PLAY (PRO)</b>\n\n"
                    f"<b>{time_casa} x {time_fora}</b>\n"
                    f"🎯 Entrada: <b>Mais de {linha_sugerida} Gols</b>\n"
                    f"⏱ Minuto: {minuto}'\n"
                    f"📊 Finalizações: {finalizacoes} (Ritmo: {ritmo_chutes:.2f}/min)"
                )
                
                await manager.broadcast({"tipo": "LIVE", "texto": texto})
                enviar_notificacao_telegram(texto)

            # --- GATILHO 2: PROJEÇÃO DE ESCANTEIOS ---
            taxa_cantos_min = cantos / minuto
            projecao_cantos = cantos + (taxa_cantos_min * (90 - minuto))
            chave_canto = f"canto_{fixture_id}"

            if 20 <= minuto <= 80 and projecao_cantos >= 8.5 and cantos >= 3 and chave_canto not in alertas_enviados_live:
                alertas_enviados_live.add(chave_canto)
                linha_canto = cantos + 2
                texto = (
                    f"🚩 <b>ALERTA DE ESCANTEIOS (PRO)</b>\n\n"
                    f"<b>{time_casa} x {time_fora}</b>\n"
                    f"🎯 Entrada: <b>Mais de {linha_canto} Escanteios</b>\n"
                    f"⏱ Minuto: {minuto}'\n"
                    f"📊 Cantos Atuais: {cantos} (Projeção: {projecao_cantos:.1f})"
                )
                
                await manager.broadcast({"tipo": "LIVE", "texto": texto})
                enviar_notificacao_telegram(texto)

            # --- GATILHO 3: CARTÕES ---
            chave_cartao = f"cartao_{fixture_id}"
            if 30 <= minuto <= 75 and total_cartoes >= 3 and chave_cartao not in alertas_enviados_live:
                alertas_enviados_live.add(chave_cartao)
                texto = (
                    f"🟨 <b>ALERTA DE CARTÕES (PRO)</b>\n\n"
                    f"<b>{time_casa} x {time_fora}</b>\n"
                    f"🎯 Entrada: <b>Mais de {total_cartoes + 1.5} Cartões</b>\n"
                    f"⏱ Minuto: {minuto}'\n"
                    f"📊 Total de Cartões: {total_cartoes}"
                )
                
                await manager.broadcast({"tipo": "LIVE", "texto": texto})
                enviar_notificacao_telegram(texto)

        # Checa jogos ao vivo a cada 30 segundos
        await asyncio.sleep(30)

async def manter_vivo():
    await asyncio.sleep(10)
    url_propria = "https://meu-bot-futebol-636r.onrender.com/"
    while True:
        try:
            requests.get(url_propria, timeout=5)
            print("[KEEP-ALIVE] Ping enviado.")
        except Exception as e:
            print(f"[KEEP-ALIVE] Erro: {e}")
        await asyncio.sleep(600)

@app.on_event("startup")
async def startup_event():
    enviar_notificacao_telegram("🚀 <b>Scanner de Futebol PRO Conectado!</b>\n\nVarredura ultrarrápida (30s) e alertas pré-jogo ativos!")
    asyncio.create_task(motor_de_analise())
    asyncio.create_task(manter_vivo())

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
