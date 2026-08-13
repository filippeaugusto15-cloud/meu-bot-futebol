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

def extrair_historico_profundo(team_id):
    """Analisa os últimos 5 jogos calculando médias reais de FT e HT."""
    url = f"{BASE_URL}/fixtures?team={team_id}&last=5"
    try:
        res = requests.get(url, headers=HEADERS, timeout=10)
        jogos = res.json().get("response", [])
        
        if not jogos:
            return None

        qtd = len(jogos)
        gols_ft = 0
        gols_ht = 0
        ambas_marcaram = 0

        for j in jogos:
            g_casa = j.get("goals", {}).get("home") or 0
            g_fora = j.get("goals", {}).get("away") or 0
            gols_ft += (g_casa + g_fora)

            if g_casa > 0 and g_fora > 0:
                ambas_marcaram += 1

            score_ht = j.get("score", {}).get("halftime", {})
            ht_casa = score_ht.get("home") or 0
            ht_fora = score_ht.get("away") or 0
            gols_ht += (ht_casa + ht_fora)

        return {
            "media_gols_ft": gols_ft / qtd,
            "media_gols_ht": gols_ht / qtd,
            "pct_btts": (ambas_marcaram / qtd) * 100,
            "qtd_jogos": qtd
        }
    except Exception as e:
        print(f"Erro no histórico do time {team_id}: {e}")
        return None

# -----------------------------------------------------------------------------
# ANÁLISE PRÉ-JOGO (ESTRITO - ALTA PROBABILIDADE ESTATÍSTICA)
# -----------------------------------------------------------------------------
async def analisar_pre_jogo():
    print("\n[PRÉ-JOGO PRO] Filtrando partidas com alta probabilidade estatística...")
    proximos = obter_proximos_jogos()

    alertas_enviados_nesta_rodada = 0

    for jogo in proximos:
        if alertas_enviados_nesta_rodada >= 3:
            break

        fixture = jogo.get("fixture", {})
        fixture_id = fixture.get("id")

        if fixture_id in alertas_enviados_pre:
            continue

        league = jogo.get("league", {})
        teams = jogo.get("teams", {})
        id_casa = teams.get("home", {}).get("id")
        nome_casa = teams.get("home", {}).get("name", "Casa")

        id_fora = teams.get("away", {}).get("id")
        nome_fora = teams.get("away", {}).get("name", "Fora")

        data_hora = fixture.get("date", "")[11:16]

        hist_casa = extrair_historico_profundo(id_casa)
        hist_fora = extrair_historico_profundo(id_fora)

        if not hist_casa or not hist_fora:
            continue

        media_gols_ft = (hist_casa["media_gols_ft"] + hist_fora["media_gols_ft"]) / 2
        media_gols_ht = (hist_casa["media_gols_ht"] + hist_fora["media_gols_ht"]) / 2
        pct_btts_media = (hist_casa["pct_btts"] + hist_fora["pct_btts"]) / 2

        sugestoes = []

        if media_gols_ht >= 1.0:
            sugestoes.append(f"• Mais de 0.5 Gols no 1º Tempo (Média HT: {media_gols_ht:.1f} gols)")

        if pct_btts_media >= 70:
            sugestoes.append(f"• Ambas as Equipes Marcam: Sim ({pct_btts_media:.0f}% de frequência)")

        if media_gols_ft >= 3.0:
            sugestoes.append(f"• Mais de 2.5 Gols na Partida (Média FT: {media_gols_ft:.1f} gols)")
        elif media_gols_ft >= 2.2:
            sugestoes.append(f"• Mais de 1.5 Gols na Partida (Média FT: {media_gols_ft:.1f} gols)")

        if media_gols_ft >= 2.8:
            sugestoes.append("• Mais de 2.5 Escanteios no 1º Tempo")
            sugestoes.append("• Mais de 8.5 Escanteios na Partida")

        if len(sugestoes) >= 2:
            alertas_enviados_pre.add(fixture_id)
            alertas_enviados_nesta_rodada += 1

            lista_sugestoes = "\n".join(sugestoes)

            texto = (
                f"🎯 <b>ANÁLISE PRÉ-JOGO (ALTA PROBABILIDADE)</b>\n\n"
                f"⚽ <b>{nome_casa} x {nome_fora}</b>\n"
                f"🏆 Liga: {league.get('name', 'Campeonato')}\n"
                f"⏰ Horário: {data_hora}\n\n"
                f"📌 <b>Oportunidades com Embasamento Estatístico:</b>\n"
                f"{lista_sugestoes}"
            )

            await manager.broadcast({"tipo": "PRE", "texto": texto})
            enviar_notificacao_telegram(texto)
            await asyncio.sleep(2)

# -----------------------------------------------------------------------------
# MOTOR PRINCIPAL (ALERTAS AO VIVO DE GOLS HT/FT, CANTOS E CARTÕES)
# -----------------------------------------------------------------------------
async def motor_de_analise():
    contador_pre = 0
    while True:
        print("\n[SCANNER PRO] Varrendo partidas ao vivo...")

        if contador_pre % 30 == 0:
            await analisar_pre_jogo()
        contador_pre += 1

        jogos_live = obter_jogos_ao_vivo()
        print(f"[LIVE] Partidas ao vivo no momento: {len(jogos_live)}")

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
            faltas = 0

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
                    elif tipo == "Yellow Cards":
                        cartoes_amarelos += val
                    elif tipo == "Red Cards":
                        cartoes_vermelhos += val
                    elif tipo == "Fouls":
                        faltas += val

            finalizacoes = chutes_gol + chutes_fora
            ritmo_chutes = finalizacoes / minuto if minuto > 0 else 0

            # -----------------------------------------------------------------
            # 1. GOL NO 1º TEMPO (HT) IN-PLAY [ENTRE 10' E 38']
            # -----------------------------------------------------------------
            chave_gol_ht = f"gol_ht_{fixture_id}"
            if 10 <= minuto <= 38 and total_gols == 0 and (ritmo_chutes >= 0.16 or chutes_gol >= 3) and chave_gol_ht not in alertas_enviados_live:
                alertas_enviados_live.add(chave_gol_ht)
                texto = (
                    f"🔥 <b>ALERTA AO VIVO: GOL NO 1º TEMPO (HT)</b>\n\n"
                    f"⚽ <b>{time_casa} x {time_fora}</b>\n"
                    f"🎯 Entrada: <b>Mais de 0.5 Gols no 1º Tempo</b>\n"
                    f"⏱ Minuto: {minuto}' (Placar: 0x0)\n"
                    f"📊 Pressão Alta: {finalizacoes} finalizações ({chutes_gol} no gol)"
                )
                await manager.broadcast({"tipo": "LIVE", "texto": texto})
                enviar_notificacao_telegram(texto)

            # -----------------------------------------------------------------
            # 2. GOL / OVER NO 2º TEMPO IN-PLAY [ENTRE 46' E 78']
            # -----------------------------------------------------------------
            chave_gol_2h = f"gol_2h_{fixture_id}"
            if 46 <= minuto <= 78 and ritmo_chutes >= 0.16 and chave_gol_2h not in alertas_enviados_live:
                alertas_enviados_live.add(chave_gol_2h)
                linha_sugerida = total_gols + 0.5
                texto = (
                    f"⚽ <b>ALERTA AO VIVO: GOLS NO 2º TEMPO</b>\n\n"
                    f"<b>{time_casa} x {time_fora}</b>\n"
                    f"🎯 Entrada: <b>Mais de {linha_sugerida} Gols na Partida</b>\n"
                    f"⏱ Minuto: {minuto}' (Placar Atual: {gols_casa}x{gols_fora})\n"
                    f"📊 Finalizações Totais: {finalizacoes} (Ritmo: {ritmo_chutes:.2f}/min)"
                )
                await manager.broadcast({"tipo": "LIVE", "texto": texto})
                enviar_notificacao_telegram(texto)

            # -----------------------------------------------------------------
            # 3. ESCANTEIOS IN-PLAY [ENTRE 20' E 80']
            # -----------------------------------------------------------------
            taxa_cantos_min = cantos / minuto if minuto > 0 else 0
            projecao_cantos = cantos + (taxa_cantos_min * (90 - minuto))
            chave_canto = f"canto_{fixture_id}"

            if 20 <= minuto <= 80 and projecao_cantos >= 9.5 and cantos >= 3 and chave_canto not in alertas_enviados_live:
                alertas_enviados_live.add(chave_canto)
                linha_canto = cantos + 2
                texto = (
                    f"🚩 <b>ALERTA AO VIVO: ESCANTEIOS IN-PLAY</b>\n\n"
                    f"<b>{time_casa} x {time_fora}</b>\n"
                    f"🎯 Entrada: <b>Mais de {linha_canto} Escanteios</b>\n"
                    f"⏱ Minuto: {minuto}'\n"
                    f"📊 Cantos Atuais: {cantos} (Projeção: {projecao_cantos:.1f})"
                )
                await manager.broadcast({"tipo": "LIVE", "texto": texto})
                enviar_notificacao_telegram(texto)

            # -----------------------------------------------------------------
            # 4. CARTÕES IN-PLAY [ENTRE 30' E 85']
            # -----------------------------------------------------------------
            total_cartoes = cartoes_amarelos + (cartoes_vermelhos * 2)
            taxa_cartoes_min = total_cartoes / minuto if minuto > 0 else 0
            projecao_cartoes = total_cartoes + (taxa_cartoes_min * (90 - minuto))
            chave_cartao = f"cartao_{fixture_id}"

            if 30 <= minuto <= 85 and (projecao_cartoes >= 4.5 or faltas >= 15) and total_cartoes >= 2 and chave_cartao not in alertas_enviados_live:
                alertas_enviados_live.add(chave_cartao)
                linha_cartao = total_cartoes + 1.5
                texto = (
                    f"🟨 <b>ALERTA AO VIVO: CARTÕES IN-PLAY</b>\n\n"
                    f"<b>{time_casa} x {time_fora}</b>\n"
                    f"🎯 Entrada: <b>Mais de {linha_cartao} Cartões na Partida</b>\n"
                    f"⏱ Minuto: {minuto}'\n"
                    f"📊 Cartões Atuais: {cartoes_amarelos} Amarelos | Faltas: {faltas}"
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
    enviar_notificacao_telegram("🚀 <b>Scanner PRO Ativado com Sucesso!</b>\n\nMonitorando ao vivo:\n• Gols HT e 2º Tempo\n• Escanteios In-Play\n• Cartões & Faltas In-Play")
    asyncio.create_task(motor_de_analise())
    asyncio.create_task(manter_vivo())

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
