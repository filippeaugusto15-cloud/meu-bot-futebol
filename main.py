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
    """Envia mensagem no celular via Telegram"""
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
        print(f"Erro no Telegram: {e}")

class ConnectionManager:
    def __init__(self):
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        if websocket in self.active_connections:
            self.active_connections.remove(websocket)

    async def broadcast(self, message: dict):
        for connection in self.active_connections:
            try:
                await connection.send_json(message)
            except Exception as e:
                print(f"Erro no WebSocket: {e}")

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
        return res.json().get("response", [])
    except Exception as e:
        print(f"Erro jogos ao vivo: {e}")
        return []

def obter_proximos_jogos():
    url = f"{BASE_URL}/fixtures?next=10"
    try:
        res = requests.get(url, headers=HEADERS, timeout=10)
        return res.json().get("response", [])
    except Exception as e:
        print(f"Erro próximos jogos: {e}")
        return []

def obter_estatisticas_detalhadas(league_id, season, team_id):
    """Busca dados de escanteios, cartões e gols da temporada do time."""
    url = f"{BASE_URL}/teams/statistics?league={league_id}&season={season}&team={team_id}"
    try:
        res = requests.get(url, headers=HEADERS, timeout=10)
        data = res.json().get("response", {})
        
        fixtures_played = data.get("fixtures", {}).get("played", {}).get("total") or 1
        
        # Gols
        goals_for = data.get("goals", {}).get("for", {}).get("average", {}).get("total") or "0"
        goals_against = data.get("goals", {}).get("against", {}).get("average", {}).get("total") or "0"
        media_gols_time = float(goals_for) + float(goals_against)
        
        # Cartões
        cards = data.get("cards", {}).get("yellow", {})
        total_cartoes = sum([v.get("total") or 0 for k, v in cards.items() if isinstance(v, dict)])
        media_cartoes = total_cartoes / fixtures_played
        
        return {
            "media_gols": media_gols_time,
            "media_cartoes": media_cartoes,
            "jogos": fixtures_played
        }
    except Exception as e:
        print(f"Erro nas estatísticas do time {team_id}: {e}")
        return {"media_gols": 0, "media_cartoes": 0, "jogos": 0}

# -----------------------------------------------------------------------------
# ANÁLISE PRÉ-JOGO REAL (ESTATÍSTICA DINÂMICA)
# -----------------------------------------------------------------------------
async def analisar_pre_jogo():
    print("\n[PRÉ-JOGO PRO] Analisando estatísticas reais da temporada...")
    proximos = obter_proximos_jogos()

    alertas_enviados_nesta_rodada = 0

    for jogo in proximos:
        if alertas_enviados_nesta_rodada >= 3: # Limite de 3 mensagens por ciclo para não sobrecarregar
            break

        fixture = jogo.get("fixture", {})
        fixture_id = fixture.get("id")

        if fixture_id in alertas_enviados_pre:
            continue

        league = jogo.get("league", {})
        league_id = league.get("id")
        season = league.get("season", 2024)

        teams = jogo.get("teams", {})
        id_casa = teams.get("home", {}).get("id")
        nome_casa = teams.get("home", {}).get("name", "Casa")

        id_fora = teams.get("away", {}).get("id")
        nome_fora = teams.get("away", {}).get("name", "Fora")

        data_hora = fixture.get("date", "")[11:16] # HH:MM

        # Consulta estatísticas completas
        stats_casa = obter_estatisticas_detalhadas(league_id, season, id_casa)
        stats_fora = obter_estatisticas_detalhadas(league_id, season, id_fora)

        if stats_casa["jogos"] < 3 or stats_fora["jogos"] < 3:
            continue

        media_gols_confronto = (stats_casa["media_gols"] + stats_fora["media_gols"]) / 2
        media_cartoes_confronto = stats_casa["media_cartoes"] + stats_fora["media_cartoes"]

        # CONSTRÓI AS SUGESTÕES COM BASE NOS NÚMEROS REAIS DA TEMPORADA
        sugestoes = []

        # Regra de Gols
        if media_gols_confronto >= 3.2:
            sugestoes.append(f"• Mais de 2.5 Gols (Média conjunta: {media_gols_confronto:.1f} gols/jogo)")
            sugestoes.append("• Ambas as Equipes Marcam (BTTS)")
        elif media_gols_confronto >= 2.3:
            sugestoes.append(f"• Mais de 1.5 Gols (Média conjunta: {media_gols_confronto:.1f} gols/jogo)")

        # Regra de Cartões
        if media_cartoes_confronto >= 5.0:
            sugestoes.append(f"• Mais de 4.5 Cartões (Média conjunta: {media_cartoes_confronto:.1f} cartões/jogo)")
        elif media_cartoes_confronto >= 3.5:
            sugestoes.append(f"• Mais de 3.5 Cartões (Média conjunta: {media_cartoes_confronto:.1f} cartões/jogo)")

        # Envia apenas se encontrou padrão estatístico claro
        if len(sugestoes) >= 2:
            alertas_enviados_pre.add(fixture_id)
            alertas_enviados_nesta_rodada += 1

            lista_sugestoes = "\n".join(sugestoes)

            texto = (
                f"📊 <b>ANÁLISE PRÉ-JOGO (ESTATÍSTICAS DA TEMPORADA)</b>\n\n"
                f"⚽ <b>{nome_casa} x {nome_fora}</b>\n"
                f"🏆 Liga: {league.get('name', 'Campeonato')}\n"
                f"⏰ Horário: {data_hora}\n\n"
                f"🎯 <b>Entradas Recomendadas baseadas em Dados:</b>\n"
                f"{lista_sugestoes}"
            )

            await manager.broadcast({"tipo": "PRE", "texto": texto})
            enviar_notificacao_telegram(texto)
            await asyncio.sleep(2) # Pequena pausa entre envios

# -----------------------------------------------------------------------------
# MOTOR PRINCIPAL
# -----------------------------------------------------------------------------
async def motor_de_analise():
    contador_pre = 0
    while True:
        print("\n[SCANNER PRO] Varrendo partidas ao vivo...")

        # Roda a análise pré-jogo a cada 30 ciclos (15 minutos)
        if contador_pre % 30 == 0:
            await analisar_pre_jogo()
        contador_pre += 1

        jogos_live = obter_jogos_ao_vivo()
        print(f"[LIVE] Partidas ao vivo: {len(jogos_live)}")

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

            # GATILHO 1: PRESSÃO DE GOLS IN-PLAY
            ritmo_chutes = finalizacoes / minuto
            chave_gol = f"gol_{fixture_id}"

            if 15 <= minuto <= 75 and ritmo_chutes >= 0.15 and chave_gol not in alertas_enviados_live:
                alertas_enviados_live.add(chave_gol)
                linha_sugerida = total_gols + 0.5
                texto = (
                    f"⚽ <b>ALERTA DE GOLS IN-PLAY</b>\n\n"
                    f"<b>{time_casa} x {time_fora}</b>\n"
                    f"🎯 Entrada: <b>Mais de {linha_sugerida} Gols</b>\n"
                    f"⏱ Minuto: {minuto}'\n"
                    f"📊 Finalizações: {finalizacoes} (Ritmo: {ritmo_chutes:.2f}/min)"
                )
                await manager.broadcast({"tipo": "LIVE", "texto": texto})
                enviar_notificacao_telegram(texto)

            # GATILHO 2: ESCANTEIOS IN-PLAY
            taxa_cantos_min = cantos / minuto
            projecao_cantos = cantos + (taxa_cantos_min * (90 - minuto))
            chave_canto = f"canto_{fixture_id}"

            if 20 <= minuto <= 80 and projecao_cantos >= 9.0 and cantos >= 3 and chave_canto not in alertas_enviados_live:
                alertas_enviados_live.add(chave_canto)
                linha_canto = cantos + 2
                texto = (
                    f"🚩 <b>ALERTA DE ESCANTEIOS IN-PLAY</b>\n\n"
                    f"<b>{time_casa} x {time_fora}</b>\n"
                    f"🎯 Entrada: <b>Mais de {linha_canto} Escanteios</b>\n"
                    f"⏱ Minuto: {minuto}'\n"
                    f"📊 Cantos Atuais: {cantos} (Projeção: {projecao_cantos:.1f})"
                )
                await manager.broadcast({"tipo": "LIVE", "texto": texto})
                enviar_notificacao_telegram(texto)

        await asyncio.sleep(30)

async def manter_vivo():
    await asyncio.sleep(10)
    url_propria = "https://meu-bot-futebol-636r.onrender.com/"
    while True:
        try:
            requests.get(url_propria, timeout=5)
        except Exception as e:
            pass
        await asyncio.sleep(600)

@app.on_event("startup")
async def startup_event():
    enviar_notificacao_telegram("🚀 <b>Scanner PRO Atualizado!</b>\n\nAnálises pré-jogo agora usam dados estatísticos reais por liga e time.")
    asyncio.create_task(motor_de_analise())
    asyncio.create_task(manter_vivo())

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
