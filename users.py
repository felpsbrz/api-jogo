import os
import secrets

from psycopg_pool import AsyncConnectionPool

DATABASE_URL = os.getenv("DATABASE_URL")
POOL_MIN = int(os.getenv("DB_POOL_MIN", "2"))
POOL_MAX = int(os.getenv("DB_POOL_MAX", "10"))

pool = AsyncConnectionPool(
    conninfo=DATABASE_URL or "",
    min_size=POOL_MIN,
    max_size=POOL_MAX,
    timeout=5,
    max_idle=300,
    max_lifetime=1800,
    check=AsyncConnectionPool.check_connection,
    kwargs={"autocommit": True, "prepare_threshold": None},
    open=False,
)

COLS = "id, cod_token, nick, pontos, level, exp, gold, diamantes, nivel_acesso, status, data_create"

SQL_LOGIN = f"SELECT {COLS} FROM usuarios WHERE cod_token = %s AND nick = %s AND status = TRUE"

SQL_CRIAR = """
INSERT INTO usuarios (cod_token, nick) VALUES (%s, %s)
ON CONFLICT DO NOTHING
RETURNING id, cod_token, nick
"""

SQL_PROGRESSO = f"""
WITH u AS (
    SELECT id,
           50::bigint * (level - 1) * level + exp + %(exp)s::bigint AS total
    FROM usuarios
    WHERE cod_token = %(token)s AND nick = %(nick)s AND status = TRUE
    FOR UPDATE
),
l AS (
    SELECT id, total,
           floor((1 + sqrt(1 + 4 * total::numeric / 50)) / 2)::int AS lv
    FROM u
)
UPDATE usuarios x
SET pontos = x.pontos + %(pontos)s::bigint,
    level = l.lv,
    exp = l.total - 50::bigint * l.lv * (l.lv - 1),
    gold = x.gold + %(gold)s::bigint,
    diamantes = x.diamantes + %(diamantes)s::bigint
FROM l
WHERE x.id = l.id
RETURNING x.{", x.".join(COLS.split(", "))}
"""


async def init_db():
    await pool.open(wait=True, timeout=15)
    async with pool.connection() as conn:
        await conn.execute("""
        CREATE TABLE IF NOT EXISTS usuarios (
            id            BIGSERIAL PRIMARY KEY,
            cod_token     TEXT UNIQUE NOT NULL,
            nick          TEXT NOT NULL,
            pontos        BIGINT DEFAULT 0,
            level         INT DEFAULT 1,
            exp           BIGINT DEFAULT 0,
            gold          BIGINT DEFAULT 0,
            diamantes     BIGINT DEFAULT 0,
            nivel_acesso  INT DEFAULT 0,
            status        BOOLEAN DEFAULT TRUE,
            data_create   TIMESTAMP DEFAULT NOW()
        )
        """)
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_usuarios_pontos ON usuarios (pontos DESC) WHERE status = TRUE")
        try:
            await conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_usuarios_nick ON usuarios (nick)")
        except Exception as e:
            print(f"aviso: indice unico de nick nao criado ({e}); existem nicks duplicados")


async def close_db():
    await pool.close()


def _row_to_dict(row) -> dict:
    return {
        "id": row[0],
        "cod_token": row[1],
        "nick": row[2],
        "pontos": row[3],
        "level": row[4],
        "exp": row[5],
        "gold": row[6],
        "diamantes": row[7],
        "nivel_acesso": row[8],
        "status": row[9],
        "data_create": row[10].isoformat() if row[10] else None,
    }


async def criar_usuario(nick: str):
    for _ in range(3):
        cod = secrets.token_hex(8)
        async with pool.connection() as conn:
            cur = await conn.execute(SQL_CRIAR, (cod, nick))
            row = await cur.fetchone()
        if row:
            return {"id": row[0], "cod_token": row[1], "nick": row[2]}
        async with pool.connection() as conn:
            cur = await conn.execute("SELECT 1 FROM usuarios WHERE nick = %s", (nick,))
            if await cur.fetchone():
                return None
    return None


async def buscar_usuario(cod_token: str, nick: str):
    async with pool.connection() as conn:
        cur = await conn.execute(SQL_LOGIN, (cod_token, nick))
        row = await cur.fetchone()
    return _row_to_dict(row) if row else None


async def adicionar_progresso(cod_token: str, nick: str, pontos=0, exp=0, gold=0, diamantes=0):
    async with pool.connection() as conn:
        cur = await conn.execute(
            SQL_PROGRESSO,
            {"token": cod_token, "nick": nick, "pontos": pontos, "exp": exp, "gold": gold, "diamantes": diamantes},
        )
        row = await cur.fetchone()
    return _row_to_dict(row) if row else None
