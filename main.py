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
    """Analisa os últimos 5 jogos gerais do time calculando médias de Gols, HT e BTTS."""
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

def extrair_h2h_finalizacoes(id_casa, id_fora):
    """Busca o histórico de confrontos diretos (H2H) para extrair médias reais de finalizações."""
    url = f"{BASE_URL}/fixtures/headtohead?h2h={id_casa}-{id_fora}&last=5"
    try:
        res = requests.get(url, headers=HEADERS, timeout=10)
        confrontos = res.json().get("response", [])

        if not confrontos:
            return None

        fin_casa_total = 0
        fin_fora_total = 0
        jogos_com_stats = 0

        for jogo in confrontos:
            fixture_id = jogo.get("fixture", {}).get("id")
            # Requisita estatísticas detalhadas do confronto
            url_stats = f"{BASE_URL}/fixtures/statistics?fixture={fixture_id}"
            res_stats = requests.get(url_stats, headers=HEADERS, timeout=10)
            stats_data = res_stats.json().get("response", [])

            if len(stats_data) >= 2:
                jogos_com_stats += 1
                for team_stats in stats_data:
                    team_id = team_stats.get("team", {}).get("id")
                    chutes_gol = 0
                    chutes_fora = 0

                    for stat in team_stats.get("statistics", []):
                        if stat.get("type") == "Shots on Goal":
                            chutes_gol = stat.get("value") or 0
                        elif stat.get("type") == "Shots off Goal":
                            chutes_fora = stat.get("value") or 0

                    fin_total = chutes_gol + chutes_fora

                    if team_id == id_casa:
                        fin_casa_total += fin_total
                    elif team_id == id_fora:
                        fin_fora_total += fin_total

        if jogos_com_stats > 0:
            return {
                "media_fin_casa": fin_casa_total / jogos_com_stats,
                "media_fin_fora": fin_fora_total / jogos_com_stats,
                "qtd_h2h": jogos_com_stats
            }
        return None
    except Exception as e:
        print(f"Erro ao buscar H2H entre {id_casa} e {id_fora}: {e}")
        return None

# -----------------------------------------------------------------------------
# ANÁLISE PRÉ-JOGO (INCLUINDO FINALIZAÇÕES VIA H2H CONFRONTO DIRETO)
# -----------------------------------------------------------------------------
async def analisar_pre_jogo():
    print("\n[PRÉ-JOGO PRO] Mapeando estatísticas exclusivas e confronto direto (H2H)...")
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

        # 1. MERCADO 1º TEMPO
        if media_gols_ht >= 1.2:
            sugestoes.append(f"• Mais de 0.5 Gols no 1º Tempo (Média HT: {media_gols_ht:.1f} gols)")

        # 2. MERCADO AMBAS MARCAM
        if pct_btts_media >= 75:
            sugestoes.append(f"• Ambas as Equipes Marcam: Sim ({pct_btts_media:.0f}% dos últimos jogos)")

        # 3. MERCADO GOLS FT
        if media_gols_ft >= 3.2:
            sugestoes.append(f"• Mais de 2.5 Gols na Partida (Média FT: {media_gols_ft:.1f} gols)")
        elif media_gols_ft >= 2.1:
            sugestoes.append(f"• Mais de 1.5 Gols na Partida (Média FT: {media_gols_ft:.1f} gols)")

        # 4. MERCADO DE FINALIZAÇÕES COM BASE NO H2H (CONFRONTO DIRETO)
        h2h_data = extrair_h2h_finalizacoes(id_casa, id_fora)
        if h2h_data:
            med_casa = h2h_data["media_fin_casa"]
            med_fora = h2h_data["media_fin_fora"]

            if med_casa >= 10.0:
                linha_sugerida = int(med_casa - 1.5) + 0.5
                sugestoes.append(f"• Mais de {linha_sugerida} Finalizações para {nome_casa} (Média H2H: {med_casa:.1f})")

            if med_fora >= 10.0:
                linha_sugerida = int(med_fora - 1.5) + 0.5
                sugestoes.append(f"• Mais de {linha_sugerida} Finalizações para {nome_fora} (Média H2H: {med_fora:.1f})")

        if len(sugestoes) >= 1:
            alertas_enviados_pre.add(fixture_id)
            alertas_enviados_nesta_rodada += 1

            lista_sugestoes = "\n".join(sugestoes)

            texto = (
                f"🎯 <b>ANÁLISE PRÉ-JOGO PERSONALIZADA</b>\n\n"
                f"⚽ <b>{nome_casa} x {nome_fora}</b>\n"
                f"🏆 Liga: {league.get('name', 'Campeonato')}\n"
                f"⏰ Horário: {data_hora}\n\n"
                f"📌 <b>Entradas Específicas Detectadas:</b>\n"
                f"{lista_sugestoes}"
            )

            await manager.broadcast({"tipo": "PRE", "texto": texto})
            enviar_notificacao_telegram(texto)
            await asyncio.sleep(2)

# -----------------------------------------------------------------------------
# MOTOR PRINCIPAL (AO VIVO: APENAS GOLS, ESCANTEIOS E CARTÕES)
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

            finalizacoes_totais = chutes_gol + chutes_fora
            ritmo_chutes_total = finalizacoes_totais / minuto if minuto > 0 else 0

            # 1. ALERTA AO VIVO: GOL 1º TEMPO (HT)
            chave_gol_ht = f"gol_ht_{fixture_id}"
            if 10 <= minuto <= 38 and total_gols == 0 and (ritmo_chutes_total >= 0.16 or chutes_gol >= 3) and chave_gol_ht not in alertas_enviados_live:
                alertas_enviados_live.add(chave_gol_ht)
                texto = (
                    f"🔥 <b>ALERTA AO VIVO: GOL NO 1º TEMPO (HT)</b>\n\n"
                    f"⚽ <b>{time_casa} x {time_fora}</b>\n"
                    f"🎯 Entrada: <b>Mais de 0.5 Gols no 1º Tempo</b>\n"
                    f"⏱ Minuto: {minuto}' (Placar: 0x0)\n"
                    f"📊 Pressão: {finalizacoes_totais} finalizações totais ({chutes_gol} no gol)"
                )
                await manager.broadcast({"tipo": "LIVE", "texto": texto})
                enviar_notificacao_telegram(texto)

            # 2. ALERTA AO VIVO: OVER GOLS 2º TEMPO
            chave_gol_2h = f"gol_2h_{fixture_id}"
            if 46 <= minuto <= 78 and ritmo_chutes_total >= 0.16 and chave_gol_2h not in alertas_enviados_live:
                alertas_enviados_live.add(chave_gol_2h)
                linha_sugerida = total_gols + 0.5
                texto = (
                    f"⚽ <b>ALERTA AO VIVO: GOLS NO 2º TEMPO</b>\n\n"
                    f"<b>{time_casa} x {time_fora}</b>\n"
                    f"🎯 Entrada: <b>Mais de {linha_sugerida} Gols na Partida</b>\n"
                    f"⏱ Minuto: {minuto}' (Placar: {gols_casa}x{gols_fora})\n"
                    f"📊 Ritmo de Jogo: {ritmo_chutes_total:.2f} chutes/min"
                )
                await manager.broadcast({"tipo": "LIVE", "texto": texto})
                enviar_notificacao_telegram(texto)

            # 3. ALERTA AO VIVO: ESCANTEIOS IN-PLAY
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

            # 4. ALERTA AO VIVO: CARTÕES IN-PLAY
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
    enviar_notificacao_telegram("🚀 <b>Scanner PRO Atualizado!</b>\n\nAnálise H2H ativada: Finalizações pré-jogo calculadas com base no histórico direto do confronto!")
    asyncio.create_task(motor_de_analise())
    asyncio.create_task(manter_vivo())

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
