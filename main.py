import asyncio
import os
import requests
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
import uvicorn

app = FastAPI()

# -----------------------------------------------------------------------------
# CONFIGURAÇÕES DE APIS E NOTIFICAÇÕES (PLANO PAGO $19 API-SPORTS)
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
        data = res.json()
        return data.get("response", [])
    except Exception as e:
        print(f"Erro ao buscar jogos ao vivo: {e}")
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
    """Analisa estatísticas recentes do time."""
    url = f"{BASE_URL}/fixtures?team={team_id}&last=5"
    try:
        res = requests.get(url, headers=HEADERS, timeout=10)
        jogos = res.json().get("response", [])
        if not jogos:
            return None

        qtd = len(jogos)
        gols_ft, gols_ht, btts = 0, 0, 0

        for j in jogos:
            gc = j.get("goals", {}).get("home") or 0
            gf = j.get("goals", {}).get("away") or 0
            gols_ft += (gc + gf)
            if gc > 0 and gf > 0:
                btts += 1

            ht = j.get("score", {}).get("halftime", {})
            gols_ht += ((ht.get("home") or 0) + (ht.get("away") or 0))

        return {
            "media_gols_ft": gols_ft / qtd,
            "media_gols_ht": gols_ht / qtd,
            "pct_btts": (btts / qtd) * 100
        }
    except Exception as e:
        return None

def extrair_h2h_finalizacoes(id_casa, id_fora):
    """Obtém histórico direto (H2H) focado em finalizações."""
    url = f"{BASE_URL}/fixtures/headtohead?h2h={id_casa}-{id_fora}&last=5"
    try:
        res = requests.get(url, headers=HEADERS, timeout=10)
        confrontos = res.json().get("response", [])
        if not confrontos:
            return None

        fin_casa, fin_fora, total_jogos = 0, 0, 0

        # Pega as estatísticas dos últimos 3 confrontos para performance ágil
        for jogo in confrontos[:3]:
            fid = jogo.get("fixture", {}).get("id")
            url_s = f"{BASE_URL}/fixtures/statistics?fixture={fid}"
            res_s = requests.get(url_s, headers=HEADERS, timeout=8)
            stats = res_s.json().get("response", [])

            if len(stats) >= 2:
                total_jogos += 1
                for t_stat in stats:
                    tid = t_stat.get("team", {}).get("id")
                    chutes = 0
                    for s in t_stat.get("statistics", []):
                        if s.get("type") in ["Total Shots", "Shots on Goal", "Shots off Goal"]:
                            chutes += (s.get("value") or 0)

                    if tid == id_casa:
                        fin_casa += chutes
                    elif tid == id_fora:
                        fin_fora += chutes

        if total_jogos > 0:
            return {
                "media_fin_casa": fin_casa / total_jogos,
                "media_fin_fora": fin_fora / total_jogos
            }
        return None
    except Exception as e:
        print(f"Erro H2H: {e}")
        return None

# -----------------------------------------------------------------------------
# ANÁLISE PRÉ-JOGO (COM FINALIZAÇÕES H2H EXCLUSIVAS)
# -----------------------------------------------------------------------------
async def analisar_pre_jogo():
    print("\n[PRÉ-JOGO PRO] Mapeando novos confrontos...")
    proximos = obter_proximos_jogos()

    alertas_enviados = 0
    for jogo in proximos:
        if alertas_enviados >= 3:
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
        pct_btts = (hist_casa["pct_btts"] + hist_fora["pct_btts"]) / 2

        sugestoes = []
        if media_gols_ht >= 1.2:
            sugestoes.append(f"• Mais de 0.5 Gols no 1º Tempo (Média HT: {media_gols_ht:.1f})")
        if pct_btts >= 70:
            sugestoes.append(f"• Ambas as Equipes Marcam: Sim ({pct_btts:.0f}% freq.)")
        if media_gols_ft >= 2.8:
            sugestoes.append(f"• Mais de 2.5 Gols na Partida (Média FT: {media_gols_ft:.1f})")

        # Busca finalizações baseada no Confronto Direto (H2H)
        h2h = extrair_h2h_finalizacoes(id_casa, id_fora)
        if h2h:
            if h2h["media_fin_casa"] >= 9.5:
                linha = round(h2h["media_fin_casa"] - 1.5) + 0.5
                sugestoes.append(f"• Mais de {linha} Finalizações para {nome_casa} (Média H2H: {h2h['media_fin_casa']:.1f})")
            if h2h["media_fin_fora"] >= 9.5:
                linha = round(h2h["media_fin_fora"] - 1.5) + 0.5
                sugestoes.append(f"• Mais de {linha} Finalizações para {nome_fora} (Média H2H: {h2h['media_fin_fora']:.1f})")

        if len(sugestoes) >= 1:
            alertas_enviados_pre.add(fixture_id)
            alertas_enviados += 1
            lista = "\n".join(sugestoes)
            texto = (
                f"🎯 <b>ANÁLISE PRÉ-JOGO PERSONALIZADA</b>\n\n"
                f"⚽ <b>{nome_casa} x {nome_fora}</b>\n"
                f"🏆 Liga: {league.get('name')}\n"
                f"⏰ Horário: {data_hora}\n\n"
                f"📌 <b>Oportunidades H2H / Estatísticas:</b>\n{lista}"
            )
            await manager.broadcast({"tipo": "PRE", "texto": texto})
            enviar_notificacao_telegram(texto)
            await asyncio.sleep(1)

# -----------------------------------------------------------------------------
# MOTOR PRINCIPAL AO VIVO (GOLS, ESCANTEIOS, CARTÕES)
# -----------------------------------------------------------------------------
async def motor_de_analise():
    contador_loop = 0
    while True:
        try:
            # Executa pré-jogo a cada 15 minutos (30 ciclos de 30 seg)
            if contador_loop % 30 == 0:
                await analisar_pre_jogo()
            contador_loop += 1

            jogos_live = obter_jogos_ao_vivo()
            print(f"[SCANNER LIVE] Partidas ao vivo monitoradas: {len(jogos_live)}")

            for jogo in jogos_live:
                fixture = jogo.get("fixture", {})
                fixture_id = fixture.get("id")
                minuto = fixture.get("status", {}).get("elapsed") or 0

                teams = jogo.get("teams", {})
                time_casa = teams.get("home", {}).get("name", "Casa")
                time_fora = teams.get("away", {}).get("name", "Fora")

                goals = jogo.get("goals", {})
                gols_casa = goals.get("home") or 0
                gols_fora = goals.get("away") or 0
                total_gols = gols_casa + gols_fora

                cantos, chutes_gol, chutes_fora, total_chutes_direct, amarelos, vermelhos, faltas = 0, 0, 0, 0, 0, 0, 0

                statistics = jogo.get("statistics", [])
                for team_stats in statistics:
                    for stat in team_stats.get("statistics", []):
                        t = stat.get("type")
                        v = stat.get("value") or 0
                        
                        if t == "Corner Kicks": cantos += v
                        elif t == "Shots on Goal": chutes_gol += v
                        elif t == "Shots off Goal": chutes_fora += v
                        elif t == "Total Shots": total_chutes_direct += v
                        elif t == "Yellow Cards": amarelos += v
                        elif t == "Red Cards": vermelhos += v
                        elif t == "Fouls": faltas += v

                finalizacoes_totais = max((chutes_gol + chutes_fora), total_chutes_direct)
                ritmo_chutes = finalizacoes_totais / minuto if minuto > 0 else 0

                # 1. ALERTA: GOL NO 1º TEMPO (HT) [10' ao 40']
                chave_ht = f"ht_{fixture_id}"
                if 10 <= minuto <= 40 and total_gols == 0 and (ritmo_chutes >= 0.10 or chutes_gol >= 2 or finalizacoes_totais >= 4) and chave_ht not in alertas_enviados_live:
                    alertas_enviados_live.add(chave_ht)
                    texto = (
                        f"🔥 <b>ALERTA AO VIVO: GOL NO 1º TEMPO (HT)</b>\n\n"
                        f"⚽ <b>{time_casa} x {time_fora}</b>\n"
                        f"🎯 Entrada sugerida: <b>Mais de 0.5 Gols HT</b>\n"
                        f"⏱ Minuto: {minuto}' (Placar: 0x0)\n"
                        f"📊 Pressão: {finalizacoes_totais} finalizações ({chutes_gol} no gol)"
                    )
                    await manager.broadcast({"tipo": "LIVE", "texto": texto})
                    enviar_notificacao_telegram(texto)

                # 2. ALERTA: OVER GOLS 2º TEMPO [46' ao 80']
                chave_2h = f"2h_{fixture_id}"
                if 46 <= minuto <= 80 and ritmo_chutes >= 0.12 and chave_2h not in alertas_enviados_live:
                    alertas_enviados_live.add(chave_2h)
                    linha = total_gols + 0.5
                    texto = (
                        f"⚽ <b>ALERTA AO VIVO: GOLS NO 2º TEMPO</b>\n\n"
                        f"⚽ <b>{time_casa} x {time_fora}</b>\n"
                        f"🎯 Entrada sugerida: <b>Mais de {linha} Gols no Jogo</b>\n"
                        f"⏱ Minuto: {minuto}' (Placar: {gols_casa}x{gols_fora})\n"
                        f"📊 Ritmo de jogo: {ritmo_chutes:.2f} chutes/min"
                    )
                    await manager.broadcast({"tipo": "LIVE", "texto": texto})
                    enviar_notificacao_telegram(texto)

                # 3. ALERTA: ESCANTEIOS [18' ao 82']
                chave_canto = f"canto_{fixture_id}"
                projecao_cantos = cantos + ((cantos / minuto) * (90 - minuto)) if minuto > 0 else 0
                if 18 <= minuto <= 82 and (projecao_cantos >= 8.5 or cantos >= 3) and chave_canto not in alertas_enviados_live:
                    alertas_enviados_live.add(chave_canto)
                    linha_c = cantos + 1.5
                    texto = (
                        f"🚩 <b>ALERTA AO VIVO: ESCANTEIOS</b>\n\n"
                        f"⚽ <b>{time_casa} x {time_fora}</b>\n"
                        f"🎯 Entrada sugerida: <b>Mais de {linha_c} Escanteios</b>\n"
                        f"⏱ Minuto: {minuto}' (Escanteios Atuais: {cantos})\n"
                        f"📊 Projeção final: {projecao_cantos:.1f}"
                    )
                    await manager.broadcast({"tipo": "LIVE", "texto": texto})
                    enviar_notificacao_telegram(texto)

                # 4. ALERTA: CARTÕES [25' ao 85']
                chave_cartao = f"cartao_{fixture_id}"
                tot_cartoes = amarelos + (vermelhos * 2)
                if 25 <= minuto <= 85 and (tot_cartoes >= 2 or faltas >= 10) and chave_cartao not in alertas_enviados_live:
                    alertas_enviados_live.add(chave_cartao)
                    linha_k = tot_cartoes + 1.5
                    texto = (
                        f"🟨 <b>ALERTA AO VIVO: CARTÕES</b>\n\n"
                        f"⚽ <b>{time_casa} x {time_fora}</b>\n"
                        f"🎯 Entrada sugerida: <b>Mais de {linha_k} Cartões</b>\n"
                        f"⏱ Minuto: {minuto}' (Cartões Atuais: {tot_cartoes})\n"
                        f"📊 Faltas até agora: {faltas}"
                    )
                    await manager.broadcast({"tipo": "LIVE", "texto": texto})
                    enviar_notificacao_telegram(texto)

        except Exception as e:
            print(f"Erro no loop principal: {e}")

        await asyncio.sleep(30)

async def manter_vivo():
    await asyncio.sleep(10)
    url_propria = "https://meu-bot-futebol-636r.onrender.com/"
    while True:
        try:
            requests.get(url_propria, timeout=5)
        except Exception:
            pass
        await asyncio.sleep(600)

@app.on_event("startup")
async def startup_event():
    enviar_notificacao_telegram("🚀 <b>Plano Pago Identificado!</b>\n\nMotor atualizado. Análise H2H de finalizações no pré-jogo e radar ao vivo 100% ativos!")
    asyncio.create_task(motor_de_analise())
    asyncio.create_task(manter_vivo())

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
