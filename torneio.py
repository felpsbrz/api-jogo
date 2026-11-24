from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from users import pool

router = APIRouter(prefix="/torneios")

PRAZO = 120
LIMITE_PARTIDA = 1200

SQL_ESTADO = """
SELECT t.nome, t.premio, t.criador, t.status, t.vencedor, t.atual, t.inscritos,
       p.ordem, p.a, p.b, p.vencedor, p.pronto_a, p.pronto_b,
       COALESCE(EXTRACT(EPOCH FROM NOW() - p.inicio), 0)::float, t.ativo
FROM torneios t JOIN torneio_partidas p ON p.torneio_id = t.id
WHERE t.id = %s ORDER BY p.ordem
"""


class Auth(BaseModel):
    cod_token: str = Field(min_length=1, max_length=64)
    nick: str = Field(min_length=1, max_length=32)


class Criar(Auth):
    nome: str = Field(min_length=1, max_length=40)
    premio: int = Field(ge=1, le=1_000_000_000)


class Ativo(Auth):
    ativo: bool


class Resultado(Auth):
    ordem: int = Field(ge=1, le=7)


async def init_torneio():
    async with pool.connection() as conn:
        await conn.execute("""
        CREATE TABLE IF NOT EXISTS torneios (
            id          BIGSERIAL PRIMARY KEY,
            nome        TEXT NOT NULL,
            premio      BIGINT NOT NULL,
            criador     TEXT NOT NULL,
            status      TEXT NOT NULL DEFAULT 'aberto',
            vencedor    TEXT,
            atual       INT NOT NULL DEFAULT 0,
            inscritos   INT NOT NULL DEFAULT 0,
            data_create TIMESTAMPTZ DEFAULT NOW()
        )
        """)
        await conn.execute("ALTER TABLE torneios ADD COLUMN IF NOT EXISTS ativo BOOLEAN NOT NULL DEFAULT TRUE")
        await conn.execute("""
        CREATE TABLE IF NOT EXISTS torneio_partidas (
            torneio_id BIGINT NOT NULL REFERENCES torneios(id) ON DELETE CASCADE,
            ordem      INT NOT NULL,
            a          TEXT,
            b          TEXT,
            vencedor   TEXT,
            pronto_a   BOOLEAN NOT NULL DEFAULT FALSE,
            pronto_b   BOOLEAN NOT NULL DEFAULT FALSE,
            inicio     TIMESTAMPTZ,
            PRIMARY KEY (torneio_id, ordem)
        )
        """)


def _fase_chave(ordem):
    if ordem <= 4:
        return 1, ordem
    if ordem <= 6:
        return 2, ordem - 4
    return 3, 1


async def _auth(conn, token, nick):
    cur = await conn.execute(
        "SELECT 1 FROM usuarios WHERE cod_token = %s AND nick = %s AND status = TRUE", (token, nick)
    )
    return await cur.fetchone() is not None


async def _travar(conn, id):
    cur = await conn.execute("SELECT 1 FROM torneios WHERE id = %s FOR UPDATE", (id,))
    return await cur.fetchone() is not None


async def _ler(conn, id):
    cur = await conn.execute(SQL_ESTADO, (id,))
    rows = await cur.fetchall()
    if not rows:
        return None
    t = rows[0]
    atual = t[5]
    decorrido = 0.0
    partidas = []
    for r in rows:
        fase, chave = _fase_chave(r[7])
        partidas.append({
            "ordem": r[7], "fase": fase, "chave": chave,
            "a": r[8] or "", "b": r[9] or "", "vencedor": r[10] or "",
            "pronto_a": r[11], "pronto_b": r[12],
            "sala": f"torneio_{id}_fase_{fase}_chave_{chave}",
        })
        if r[7] == atual:
            decorrido = r[13]
    andamento = t[3] == "andamento"
    return {
        "id": id, "nome": t[0], "premio": t[1], "criador": t[2], "status": t[3],
        "vencedor": t[4] or "", "atual": atual, "inscritos": t[6],
        "restante": max(0, int(PRAZO - decorrido)) if andamento else 0,
        "decorrido": decorrido, "partidas": partidas, "ativo": t[14],
    }


def _por_tempo(p, decorrido):
    if p["vencedor"] or decorrido < PRAZO:
        return None
    if p["pronto_a"] and not p["pronto_b"]:
        return p["a"]
    if p["pronto_b"] and not p["pronto_a"]:
        return p["b"]
    if (not p["pronto_a"] and not p["pronto_b"]) or decorrido >= LIMITE_PARTIDA:
        return p["a"]
    return None


async def _definir(conn, id, e, vencedor):
    ordem = e["atual"]
    await conn.execute(
        "UPDATE torneio_partidas SET vencedor = %s WHERE torneio_id = %s AND ordem = %s",
        (vencedor, id, ordem),
    )
    if ordem == 7:
        await conn.execute("UPDATE torneios SET status = 'finalizado', vencedor = %s WHERE id = %s", (vencedor, id))
        await conn.execute(
            "UPDATE usuarios SET diamantes = diamantes + %s::bigint WHERE nick = %s", (e["premio"], vencedor)
        )
        return
    destino = 5 + (ordem - 1) // 2 if ordem <= 4 else 7
    lado = "a" if ordem % 2 == 1 else "b"
    await conn.execute(
        f"UPDATE torneio_partidas SET {lado} = %s WHERE torneio_id = %s AND ordem = %s",
        (vencedor, id, destino),
    )
    await conn.execute("UPDATE torneios SET atual = %s WHERE id = %s", (ordem + 1, id))
    await conn.execute(
        "UPDATE torneio_partidas SET inicio = NOW() WHERE torneio_id = %s AND ordem = %s", (id, ordem + 1)
    )


async def _sincronizar(conn, id):
    e = await _ler(conn, id)
    if e and e["status"] == "andamento":
        v = _por_tempo(e["partidas"][e["atual"] - 1], e["decorrido"])
        if v:
            await _definir(conn, id, e, v)
            e = await _ler(conn, id)
    return e


@router.get("")
async def listar():
    async with pool.connection() as conn:
        cur = await conn.execute(
            "SELECT id, nome, premio, status, inscritos, ativo, criador FROM torneios "
            "ORDER BY (status = 'aberto') DESC, id DESC LIMIT 30"
        )
        rows = await cur.fetchall()
    return {"torneios": [
        {"id": r[0], "nome": r[1], "premio": r[2], "status": r[3], "inscritos": r[4], "ativo": r[5], "criador": r[6]}
        for r in rows
    ]}


@router.post("/criar")
async def criar(b: Criar):
    nome = b.nome.strip()
    if not nome:
        raise HTTPException(422, "Nome inválido.")
    async with pool.connection() as conn:
        async with conn.transaction():
            cur = await conn.execute(
                "UPDATE usuarios SET diamantes = diamantes - %s::bigint "
                "WHERE cod_token = %s AND nick = %s AND status = TRUE AND diamantes >= %s::bigint RETURNING 1",
                (b.premio, b.cod_token, b.nick, b.premio),
            )
            if not await cur.fetchone():
                if await _auth(conn, b.cod_token, b.nick):
                    raise HTTPException(0, "Diamantes insuficientes.")
                raise HTTPException(401, "cod_token ou nick inválidos.")
            cur = await conn.execute(
                "INSERT INTO torneios (nome, premio, criador) VALUES (%s, %s, %s) RETURNING id",
                (nome, b.premio, b.nick),
            )
            id = (await cur.fetchone())[0]
            await conn.execute(
                "INSERT INTO torneio_partidas (torneio_id, ordem) SELECT %s::bigint, g FROM generate_series(1, 7) g",
                (id,),
            )
    return {"id": id}


@router.post("/{id}/ativo")
async def definir_ativo(id: int, b: Ativo):
    async with pool.connection() as conn:
        async with conn.transaction():
            if not await _auth(conn, b.cod_token, b.nick):
                raise HTTPException(401, "cod_token ou nick inválidos.")
            cur = await conn.execute("SELECT criador FROM torneios WHERE id = %s FOR UPDATE", (id,))
            t = await cur.fetchone()
            if not t:
                raise HTTPException(404, "Torneio não existe.")
            if t[0] != b.nick:
                raise HTTPException(403, "Apenas o criador pode ativar ou desativar.")
            await conn.execute("UPDATE torneios SET ativo = %s WHERE id = %s", (b.ativo, id))
    return {"id": id, "ativo": b.ativo}


@router.get("/{id}")
async def estado(id: int):
    async with pool.connection() as conn:
        e = await _ler(conn, id)
        if not e:
            raise HTTPException(404, "Torneio não existe.")
        if e["status"] == "andamento" and _por_tempo(e["partidas"][e["atual"] - 1], e["decorrido"]):
            async with conn.transaction():
                if await _travar(conn, id):
                    e = await _sincronizar(conn, id)
    return e


@router.post("/{id}/inscrever")
async def inscrever(id: int, b: Auth):
    async with pool.connection() as conn:
        async with conn.transaction():
            if not await _auth(conn, b.cod_token, b.nick):
                raise HTTPException(401, "cod_token ou nick inválidos.")
            cur = await conn.execute("SELECT status, inscritos, ativo FROM torneios WHERE id = %s FOR UPDATE", (id,))
            t = await cur.fetchone()
            if not t:
                raise HTTPException(404, "Torneio não existe.")
            if t[0] != "aberto":
                raise HTTPException(409, "Inscrições encerradas.")
            if not t[2]:
                raise HTTPException(409, "Torneio desativado.")
            cur = await conn.execute(
                "SELECT 1 FROM torneio_partidas WHERE torneio_id = %s AND ordem <= 4 AND (a = %s OR b = %s)",
                (id, b.nick, b.nick),
            )
            if await cur.fetchone():
                raise HTTPException(409, "Você já está inscrito.")
            n = t[1]
            lado = "a" if n % 2 == 0 else "b"
            await conn.execute(
                f"UPDATE torneio_partidas SET {lado} = %s WHERE torneio_id = %s AND ordem = %s",
                (b.nick, id, n // 2 + 1),
            )
            if n + 1 == 8:
                await conn.execute("UPDATE torneios SET inscritos = 8, status = 'andamento', atual = 1 WHERE id = %s", (id,))
                await conn.execute("UPDATE torneio_partidas SET inicio = NOW() WHERE torneio_id = %s AND ordem = 1", (id,))
            else:
                await conn.execute("UPDATE torneios SET inscritos = inscritos + 1 WHERE id = %s", (id,))
            return await _ler(conn, id)


@router.post("/{id}/pronto")
async def pronto(id: int, b: Auth):
    async with pool.connection() as conn:
        async with conn.transaction():
            if not await _auth(conn, b.cod_token, b.nick):
                raise HTTPException(401, "cod_token ou nick inválidos.")
            if not await _travar(conn, id):
                raise HTTPException(404, "Torneio não existe.")
            e = await _sincronizar(conn, id)
            if e["status"] != "andamento":
                raise HTTPException(409, "Torneio não está em andamento.")
            p = e["partidas"][e["atual"] - 1]
            if b.nick not in (p["a"], p["b"]):
                raise HTTPException(409, "Não é a sua vez.")
            col = "pronto_a" if b.nick == p["a"] else "pronto_b"
            await conn.execute(
                f"UPDATE torneio_partidas SET {col} = TRUE WHERE torneio_id = %s AND ordem = %s", (id, p["ordem"])
            )
    return {"sala": p["sala"], "ordem": p["ordem"]}


@router.post("/{id}/resultado")
async def resultado(id: int, b: Resultado):
    async with pool.connection() as conn:
        async with conn.transaction():
            if not await _auth(conn, b.cod_token, b.nick):
                raise HTTPException(401, "cod_token ou nick inválidos.")
            if not await _travar(conn, id):
                raise HTTPException(404, "Torneio não existe.")
            e = await _sincronizar(conn, id)
            if e["status"] != "andamento":
                raise HTTPException(409, "Torneio não está em andamento.")
            p = e["partidas"][e["atual"] - 1]
            if p["ordem"] != b.ordem or b.nick not in (p["a"], p["b"]):
                raise HTTPException(409, "Partida inválida.")
            await _definir(conn, id, e, b.nick)
    return {"ok": True}
