"""
main.py — API Game (FastAPI) — Render + Neon Postgres.
Execute local:  uvicorn main:app --reload
No Render:      uvicorn main:app --host 0.0.0.0 --port $PORT
"""
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

import users
import rank
import reward

app = FastAPI(title="API Game", version="1.0.0")


@app.on_event("startup")
def startup():
    users.init_db()


# ---------- Schemas ----------
class CriarUsuario(BaseModel):
    nick: str


class Auth(BaseModel):
    """Toda requisição protegida envia cod_token + nick."""
    cod_token: str
    nick: str


class Progresso(Auth):
    pontos: int = 0
    exp: int = 0
    gold: int = 0
    diamantes: int = 0


# ---------- Health ----------
@app.get("/")
def health():
    return {"status": "online", "api": "API Game v1.0.0"}


# ---------- Usuários ----------
@app.post("/usuarios/criar")
def criar(body: CriarUsuario):
    if users.existe_nick(body.nick):
        raise HTTPException(409, "Nick já está em uso.")
    return users.criar_usuario(body.nick)


@app.post("/usuarios/login")
def login(body: Auth):
    """Login + recuperação de conta: basta cod_token + nick."""
    u = users.buscar_usuario(body.cod_token, body.nick)
    if not u:
        raise HTTPException(401, "cod_token ou nick inválidos.")
    return u


@app.post("/usuarios/progresso")
def progresso(body: Progresso):
    u = users.adicionar_progresso(
        body.cod_token, body.nick, body.pontos, body.exp, body.gold, body.diamantes
    )
    if not u:
        raise HTTPException(401, "cod_token ou nick inválidos.")
    return u


# ---------- Rank ----------
@app.get("/rank")
def rank_geral(limite: int = 10):
    return {"ranking": rank.ranking_geral(limite)}


@app.get("/rank/mes")
def rank_mes(limite: int = 10):
    return {"ranking": rank.ranking_mes(limite)}


# ---------- Recompensas ----------
@app.post("/recompensas/tempo")
def rec_tempo(body: Auth):
    """Resgata a recompensa de tempo logado (a cada 10 min)."""
    r = reward.recompensa_tempo(body.cod_token, body.nick)
    if r is None:
        raise HTTPException(401, "cod_token ou nick inválidos.")
    if "erro" in r:
        raise HTTPException(429, r["erro"])
    return r


@app.post("/recompensas/conceder")
def rec_conceder(body: Auth, tipo: str):
    """Concede uma recompensa fixa (ex.: 'top1_mes') — use com nível_acesso admin."""
    r = reward.conceder_recompensa(body.cod_token, body.nick, tipo)
    if r is None:
        raise HTTPException(401, "cod_token ou nick inválidos.")
    if "erro" in r:
        raise HTTPException(400, r["erro"])
    return r


@app.post("/admin/recompensas/top-mes")
def admin_top_mes():
    """Premia o top 3 do mês. Proteja com um header de admin em produção."""
    return reward.recompensas_top_mes()
