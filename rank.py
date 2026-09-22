"""
rank.py — Rankings: geral e do mês corrente.
"""
from datetime import datetime

from users import get_conn


def _inicio_do_mes() -> datetime:
    return datetime.now().replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def ranking_geral(limite: int = 10) -> list:
    with get_conn() as conn:
        cur = conn.execute(
            """SELECT nick, pontos, level, gold, diamantes FROM usuarios
               WHERE status = TRUE ORDER BY pontos DESC LIMIT %s""",
            (limite,),
        )
        return [
            {"posicao": i + 1, "nick": r[0], "pontos": r[1],
             "level": r[2], "gold": r[3], "diamantes": r[4]}
            for i, r in enumerate(cur.fetchall())
        ]


def ranking_mes(limite: int = 10) -> list:
    """Rank do mês (usuários criados neste mês; zere os pontos mensais via cron se preferir)."""
    with get_conn() as conn:
        cur = conn.execute(
            """SELECT nick, pontos, level FROM usuarios
               WHERE status = TRUE AND data_create >= %s
               ORDER BY pontos DESC LIMIT %s""",
            (_inicio_do_mes(), limite),
        )
        return [
            {"posicao": i + 1, "nick": r[0], "pontos": r[1], "level": r[2]}
            for i, r in enumerate(cur.fetchall())
        ]


def top_mes(posicao: int = 1):
    """Retorna o usuário em dada posição no mês (usado para premiar o top)."""
    with get_conn() as conn:
        cur = conn.execute(
            """SELECT id, nick, pontos FROM usuarios
               WHERE status = TRUE AND data_create >= %s
               ORDER BY pontos DESC OFFSET %s LIMIT 1""",
            (_inicio_do_mes(), posicao - 1),
        )
        row = cur.fetchone()
    if not row:
        return None
    return {"id": row[0], "nick": row[1], "pontos": row[2]}
