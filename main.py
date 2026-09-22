from fastapi import FastAPI, Depends, HTTPException
from pydantic import BaseModel
import sqlite3
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

# Importa o sistema de chat (auth helpers ficam lá para evitar import circular)
from chat import (
    router as chat_router,
    init_chat,
    conectar,
    gerar_token,
    hash_senha,
    get_current_user,
    get_cooldown,
)

# --- APP ---
app = FastAPI(title="API do Meu Jogo")

# --- CORS ---
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- BANCO DE DADOS ---
# Cria a tabela de jogadores automaticamente ao iniciar
conn = conectar()
conn.execute("""
    CREATE TABLE IF NOT EXISTS jogadores (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nick TEXT UNIQUE NOT NULL,
        senha_hash TEXT,
        token TEXT,
        cargo TEXT DEFAULT 'user',
        pontos INTEGER DEFAULT 0,
        level INTEGER DEFAULT 1,
        ban_ate REAL DEFAULT 0,
        ban_perm INTEGER DEFAULT 0,
        ultima_msg REAL DEFAULT 0,
        ultimo_grito REAL DEFAULT 0
    )
""")
conn.commit()
conn.close()

# Cria as tabelas do chat (mensagens, config) e migra colunas novas
init_chat()

# --- MODELOS ---
class Jogador(BaseModel):
    nick: str
    senha: str
    pontos: int = 0
    level: int = 1

class Login(BaseModel):
    nick: str
    senha: str

class Renomear(BaseModel):
    novo_nick: str

class Promover(BaseModel):
    nick: str
    cargo: str  # 'user', 'mod' ou 'admin'

# --- ROTAS DE CONTA ---

# Cadastrar jogador (o PRIMEIRO usuário vira admin automaticamente!)
@app.post("/jogadores")
def criar_jogador(jogador: Jogador):
    conn = conectar()
    try:
        total = conn.execute("SELECT COUNT(*) AS c FROM jogadores").fetchone()["c"]
        cargo = "admin" if total == 0 else "user"  # 👑 primeiro usuário = admin

        token = gerar_token()
        cursor = conn.execute(
            """INSERT INTO jogadores
               (nick, senha_hash, token, cargo, pontos, level)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (jogador.nick, hash_senha(jogador.senha), token, cargo,
             jogador.pontos, jogador.level)
        )
        conn.commit()
        return {
            "id": cursor.lastrowid,
            "msg": "Jogador criado!",
            "token": token,
            "cargo": cargo,
        }
    except sqlite3.IntegrityError:
        return {"erro": "Esse nick já existe!"}
    finally:
        conn.close()

# Login → devolve o token
@app.post("/login")
def login(dados: Login):
    conn = conectar()
    jogador = conn.execute(
        "SELECT * FROM jogadores WHERE nick = ?", (dados.nick,)
    ).fetchone()
    conn.close()

    if not jogador or jogador["senha_hash"] != hash_senha(dados.senha):
        raise HTTPException(401, "Nick ou senha incorretos!")

    token = gerar_token()
    conn = conectar()
    conn.execute("UPDATE jogadores SET token = ? WHERE id = ?",
                 (token, jogador["id"]))
    conn.commit()
    conn.close()

    return {
        "msg": "Login OK!",
        "token": token,
        "nick": jogador["nick"],
        "cargo": jogador["cargo"],
        "cooldown": get_cooldown(),
    }

# Listar todos (rota pública, útil pro ranking depois)
@app.get("/jogadores")
def listar_jogadores():
    conn = conectar()
    jogadores = conn.execute(
        "SELECT id, nick, cargo, pontos, level FROM jogadores"
    ).fetchall()
    conn.close()
    return [dict(j) for j in jogadores]

# Ver jogador pelo nick
@app.get("/jogadores/{nick}")
def ver_jogador(nick: str):
    conn = conectar()
    jogador = conn.execute(
        "SELECT id, nick, cargo, pontos, level FROM jogadores WHERE nick = ?",
        (nick,)
    ).fetchone()
    conn.close()
    if jogador:
        return dict(jogador)
    return {"erro": "Jogador não encontrado!"}

# Atualizar pontos/level (precisa de token; só edita a própria conta, admin edita qualquer)
@app.put("/jogadores/{nick}")
def atualizar_jogador(nick: str, jogador: Jogador,
                      user=Depends(get_current_user)):
    if user["nick"] != nick and user["cargo"] != "admin":
        raise HTTPException(403, "Você só pode editar a própria conta!")

    conn = conectar()
    cursor = conn.execute(
        "UPDATE jogadores SET pontos = ?, level = ? WHERE nick = ?",
        (jogador.pontos, jogador.level, nick)
    )
    conn.commit()
    conn.close()

    if cursor.rowcount == 0:
        return {"erro": "Jogador não encontrado!"}
    return {"msg": "Atualizado!"}

# Deletar jogador (só a própria conta ou admin)
@app.delete("/jogadores/{nick}")
def deletar_jogador(nick: str, user=Depends(get_current_user)):
    if user["nick"] != nick and user["cargo"] != "admin":
        raise HTTPException(403, "Sem permissão!")

    conn = conectar()
    cursor = conn.execute("DELETE FROM jogadores WHERE nick = ?", (nick,))
    conn.commit()
    conn.close()

    if cursor.rowcount == 0:
        return {"erro": "Jogador não encontrado!"}
    return {"msg": "Deletado!"}

# Alterar nick (só a própria conta)
@app.patch("/jogadores/{nick}")
def renomear(nick: str, data: Renomear,
             user=Depends(get_current_user)):
    if user["nick"] != nick and user["cargo"] != "admin":
        raise HTTPException(403, "Sem permissão!")

    conn = conectar()
    try:
        cursor = conn.execute(
            "UPDATE jogadores SET nick = ? WHERE nick = ?",
            (data.novo_nick, nick)
        )
        conn.commit()
        if cursor.rowcount == 0:
            return {"erro": "Jogador não encontrado!"}
        return {"msg": f"Nick alterado para {data.novo_nick}!"}
    except sqlite3.IntegrityError:
        return {"erro": "Esse nick já está em uso!"}
    finally:
        conn.close()

# Promover/despromover usuário (só admin)
@app.post("/admin/promover")
def promover(dados: Promover, user=Depends(get_current_user)):
    if user["cargo"] != "admin":
        raise HTTPException(403, "Só admin pode promover!")
    if dados.cargo not in ("user", "mod", "admin"):
        raise HTTPException(400, "Cargo inválido! Use: user, mod ou admin")

    conn = conectar()
    cursor = conn.execute(
        "UPDATE jogadores SET cargo = ? WHERE nick = ?",
        (dados.cargo, dados.nick)
    )
    conn.commit()
    conn.close()

    if cursor.rowcount == 0:
        return {"erro": "Jogador não encontrado!"}
    return {"msg": f"{dados.nick} agora é {dados.cargo}!"}

# --- ROTAS DE PÁGINAS DE TESTE ---
@app.get("/teste")
def pagina_teste():
    return FileResponse("teste.html")

@app.get("/teste_chat")
def pagina_teste_chat():
    return FileResponse("teste_chat.html")

# Registra as rotas do chat (/chat/enviar, /chat/mensagens)
app.include_router(chat_router)
