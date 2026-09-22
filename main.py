"""
main.py — API Game (FastAPI) — Render + Neon Postgres.
Local:   uvicorn main:app --reload
Render:  uvicorn main:app --host 0.0.0.0 --port $PORT
"""
import time
import json
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel

import users
import rank
import reward

app = FastAPI(title="API Game", version="2.0.0")

# Libera chamadas do navegador (página de teste / futuro front-end)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.on_event("startup")
def startup():
    for tentativa in range(5):
        try:
            users.init_db()
            return
        except Exception:
            if tentativa == 4:
                raise
            time.sleep(3)

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
    return {"status": "online", "api": "API Game v2.0.0"}

# ---------- Página de teste ----------
@app.get("/teste")
def pagina_teste():
    return FileResponse("teste.html")

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
    """Concede uma recompensa fixa (ex.: 'top1_mes')."""
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

# ==================== WEBSOCKET (TEMPO REAL) ====================
class ConnectionManager:
    """Gerencia conexões WebSocket ativas."""
    def __init__(self):
        self.active: dict[str, WebSocket] = {}  # cod_token -> websocket

    async def connect(self, cod_token: str, websocket: WebSocket):
        await websocket.accept()
        self.active[cod_token] = websocket

    def disconnect(self, cod_token: str):
        if cod_token in self.active:
            del self.active[cod_token]

    async def send_to(self, cod_token: str, message: dict):
        ws = self.active.get(cod_token)
        if ws:
            try:
                await ws.send_text(json.dumps(message))
            except Exception:
                self.disconnect(cod_token)

manager = ConnectionManager()

@app.websocket("/ws/{cod_token}/{nick}")
async def websocket_endpoint(websocket: WebSocket, cod_token: str, nick: str):
    """
    Conexão persistente: 1 handshake só, depois tudo em <10ms.
    Mensagens (JSON):
      → {"acao": "progresso", "pontos": 10, "exp": 20, "gold": 0, "diamantes": 0}
      → {"acao": "recompensa_tempo"}
      → {"acao": "rank"}
      → {"acao": "ping"}
    ← Respostas: {"tipo": "...", "dados": {...}}
    """
    # Autentica na conexão
    user = users.buscar_usuario(cod_token, nick)
    if not user:
        await websocket.close(code=4401)
        return

    await manager.connect(cod_token, websocket)
    await websocket.send_text(json.dumps({
        "tipo": "conectado",
        "dados": user
    }))

    try:
        while True:
            data = await websocket.receive_text()
            msg = json.loads(data)
            acao = msg.get("acao")

            if acao == "ping":
                await websocket.send_text(json.dumps({"tipo": "pong", "timestamp": time.time()}))

            elif acao == "progresso":
                u = users.adicionar_progresso(
                    cod_token, nick,
                    msg.get("pontos", 0), msg.get("exp", 0),
                    msg.get("gold", 0), msg.get("diamantes", 0)
                )
                await websocket.send_text(json.dumps({"tipo": "progresso", "dados": u}))

            elif acao == "recompensa_tempo":
                r = reward.recompensa_tempo(cod_token, nick)
                if r and "erro" not in r:
                    await websocket.send_text(json.dumps({"tipo": "recompensa", "dados": r}))
                else:
                    await websocket.send_text(json.dumps({"tipo": "erro", "dados": r}))

            elif acao == "rank":
                await websocket.send_text(json.dumps({
                    "tipo": "rank",
                    "dados": {"ranking": rank.ranking_geral(10)}
                }))

    except WebSocketDisconnect:
        manager.disconnect(cod_token)
    except Exception as e:
        manager.disconnect(cod_token)
