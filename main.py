import asyncio
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

import rank
import torneio
import users

PAGINA_TESTE = Path(__file__).with_name("teste.html").read_text(encoding="utf-8")
PAGINA_TESTE_TORNEIO = Path(__file__).with_name("testeTorneio.html").read_text(encoding="utf-8")


@asynccontextmanager
async def lifespan(app: FastAPI):
    for tentativa in range(5):
        try:
            await users.init_db()
            await torneio.init_torneio()
            break
        except Exception:
            if tentativa == 4:
                raise
            await asyncio.sleep(3)
    yield
    await users.close_db()


class TempoResposta:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        inicio = time.perf_counter()

        async def enviar(msg):
            if msg["type"] == "http.response.start":
                ms = (time.perf_counter() - inicio) * 1000
                msg.setdefault("headers", []).append((b"x-process-time", f"{ms:.2f}".encode()))
            await send(msg)

        await self.app(scope, receive, enviar)


app = FastAPI(title="API Game", version="2.0.0", lifespan=lifespan, docs_url=None, redoc_url=None)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Process-Time"],
)
app.add_middleware(TempoResposta)
app.include_router(torneio.router)


class CriarUsuario(BaseModel):
    nick: str = Field(min_length=1, max_length=32)


class Auth(BaseModel):
    cod_token: str = Field(min_length=1, max_length=64)
    nick: str = Field(min_length=1, max_length=32)


class Progresso(Auth):
    pontos: int = Field(0, ge=0, le=1_000_000_000)
    exp: int = Field(0, ge=0, le=1_000_000_000)
    gold: int = Field(0, ge=0, le=1_000_000_000)
    diamantes: int = Field(0, ge=0, le=1_000_000_000)


@app.get("/")
async def health():
    return {"status": "online", "api": "API Game v2.0.0"}


@app.get("/teste", response_class=HTMLResponse)
async def pagina_teste():
    return PAGINA_TESTE


@app.get("/teste-torneio", response_class=HTMLResponse)
async def pagina_teste_torneio():
    return PAGINA_TESTE_TORNEIO


@app.post("/usuarios/criar")
async def criar(body: CriarUsuario):
    nick = body.nick.strip()
    if not nick:
        raise HTTPException(422, "Nick inválido.")
    u = await users.criar_usuario(nick)
    if not u:
        raise HTTPException(409, "Nick já está em uso.")
    return u


@app.post("/usuarios/login")
async def login(body: Auth):
    u = await users.buscar_usuario(body.cod_token, body.nick)
    if not u:
        raise HTTPException(401, "cod_token ou nick inválidos.")
    return u


@app.post("/usuarios/progresso")
async def progresso(body: Progresso):
    u = await users.adicionar_progresso(
        body.cod_token, body.nick, body.pontos, body.exp, body.gold, body.diamantes
    )
    if not u:
        raise HTTPException(401, "cod_token ou nick inválidos.")
    return u


@app.get("/rank")
async def rank_geral(limite: int = Query(10, ge=1, le=100)):
    return {"ranking": await rank.ranking_geral(limite)}


@app.get("/rank/mes")
async def rank_mes(limite: int = Query(10, ge=1, le=100)):
    return {"ranking": await rank.ranking_mes(limite)}
