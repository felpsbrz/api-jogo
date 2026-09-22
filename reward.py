"""
reward.py — Central de recompensas. TODAS as recompensas do jogo ficam aqui.
Para balancear o jogo, basta editar o dicionário RECOMPENSAS.
"""
import json
import random
from datetime import datetime, timedelta

from users import get_conn, buscar_usuario

# ===================== CENTRAL DE RECOMPENSAS =====================
RECOMPENSAS = {
    # Ranking mensal
    "top1_mes": {"diamantes": 1000, "gold": 500},
    "top2_mes": {"diamantes": 500,  "gold": 250},
    "top3_mes": {"diamantes": 250,  "gold": 100},
    # Tempo logado: a cada 10 min de jogo
    "tempo_10min_min": 10,
    "tempo_10min_max": 100,
}
# ==================================================================


def _registrar_log(conn, user_id: int, tipo: str, recompensa: dict):
    conn.execute(
        "INSERT INTO reward_log (user_id, tipo, recompensa) VALUES (%s, %s, %s)",
        (user_id, tipo, json.dumps(recompensa)),
    )


def conceder_recompensa(cod_token: str, nick: str, tipo: str):
    """Concede uma recompensa fixa cadastrada em RECOMPENSAS."""
    user = buscar_usuario(cod_token, nick)
    if not user:
        return None
    rec = RECOMPENSAS.get(tipo)
    if not rec:
        return {"erro": f"recompensa '{tipo}' não existe"}
    gold, diamantes = rec.get("gold", 0), rec.get("diamantes", 0)
    with get_conn() as conn:
        conn.execute(
            "UPDATE usuarios SET gold = gold + %s, diamantes = diamantes + %s WHERE id = %s",
            (gold, diamantes, user["id"]),
        )
        _registrar_log(conn, user["id"], tipo, rec)
    return {"tipo": tipo, "recompensa": rec, "saldo": buscar_usuario(cod_token, nick)}


def recompensa_tempo(cod_token: str, nick: str):
    """A cada 10 min de jogo: ganha 10–100 gold OU 10–100 diamantes (aleatório)."""
    user = buscar_usuario(cod_token, nick)
    if not user:
        return None

    agora = datetime.now()
    with get_conn() as conn:
        cur = conn.execute(
            """SELECT data_claim FROM reward_log
               WHERE user_id = %s AND tipo = 'tempo_10min'
               ORDER BY data_claim DESC LIMIT 1""",
            (user["id"],),
        )
        ultima = cur.fetchone()
        if ultima and agora - ultima[0] < timedelta(minutes=10):
            restante = int((timedelta(minutes=10) - (agora - ultima[0])).total_seconds())
            return {"erro": f"Aguarde {restante}s para resgatar a próxima recompensa."}

        moeda = random.choice(["gold", "diamantes"])
        valor = random.randint(RECOMPENSAS["tempo_10min_min"], RECOMPENSAS["tempo_10min_max"])
        conn.execute(f"UPDATE usuarios SET {moeda} = {moeda} + %s WHERE id = %s", (valor, user["id"]))
        rec = {moeda: valor}
        _registrar_log(conn, user["id"], "tempo_10min", rec)

    return {"tipo": "tempo_10min", "recompensa": rec, "saldo": buscar_usuario(cod_token, nick)}


def recompensas_top_mes():
    """Concede as recompensas do top 3 do mês. Rode no dia 1º via cron."""
    from rank import top_mes  # import local para evitar ciclo
    premiacao = [(1, "top1_mes"), (2, "top2_mes"), (3, "top3_mes")]
    resultados = []
    for posicao, tipo in premiacao:
        topo = top_mes(posicao)
        if not topo:
            continue
        rec = RECOMPENSAS[tipo]
        with get_conn() as conn:
            conn.execute(
                "UPDATE usuarios SET gold = gold + %s, diamantes = diamantes + %s WHERE id = %s",
                (rec.get("gold", 0), rec.get("diamantes", 0), topo["id"]),
            )
            _registrar_log(conn, topo["id"], tipo, rec)
        resultados.append({"posicao": posicao, "nick": topo["nick"], "recompensa": rec})
    return {"premiados": resultados}
