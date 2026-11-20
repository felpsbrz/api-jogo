import time

from users import pool

CACHE_TTL = 3.0
_cache = {}

SQL_GERAL = """
SELECT nick, pontos, level, gold, diamantes FROM usuarios
WHERE status = TRUE ORDER BY pontos DESC LIMIT %s
"""

SQL_MES = """
SELECT nick, pontos, level FROM usuarios
WHERE status = TRUE AND data_create >= date_trunc('month', LOCALTIMESTAMP)
ORDER BY pontos DESC LIMIT %s
"""


async def _consultar(chave: str, sql: str, limite: int, montar):
    agora = time.monotonic()
    hit = _cache.get((chave, limite))
    if hit and hit[0] > agora:
        return hit[1]
    async with pool.connection() as conn:
        cur = await conn.execute(sql, (limite,))
        rows = await cur.fetchall()
    dados = [montar(i + 1, r) for i, r in enumerate(rows)]
    _cache[(chave, limite)] = (agora + CACHE_TTL, dados)
    return dados


async def ranking_geral(limite: int = 10) -> list:
    return await _consultar(
        "geral", SQL_GERAL, limite,
        lambda i, r: {"posicao": i, "nick": r[0], "pontos": r[1], "level": r[2], "gold": r[3], "diamantes": r[4]},
    )


async def ranking_mes(limite: int = 10) -> list:
    return await _consultar(
        "mes", SQL_MES, limite,
        lambda i, r: {"posicao": i, "nick": r[0], "pontos": r[1], "level": r[2]},
    )
