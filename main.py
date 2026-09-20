from fastapi import FastAPI
from pydantic import BaseModel
import sqlite3
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
app = FastAPI(title="API do Meu Jogo")

# --- BANCO DE DADOS ---
def conectar():
    conn = sqlite3.connect("jogo.db")
    conn.row_factory = sqlite3.Row  # permite acessar colunas pelo nome
    return conn

# Cria a tabela automaticamente ao iniciar
conn = conectar()
conn.execute("""
    CREATE TABLE IF NOT EXISTS jogadores (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nick TEXT UNIQUE NOT NULL,
        pontos INTEGER DEFAULT 0,
        level INTEGER DEFAULT 1
    )
""")
conn.commit()
conn.close()

# --- MODELOS ---
class Jogador(BaseModel):
    nick: str
    pontos: int = 0
    level: int = 1

# --- ROTAS ---

# Cadastrar jogador (POST)
@app.post("/jogadores")
def criar_jogador(jogador: Jogador):
    conn = conectar()
    try:
        cursor = conn.execute(
            "INSERT INTO jogadores (nick, pontos, level) VALUES (?, ?, ?)",
            (jogador.nick, jogador.pontos, jogador.level)
        )
        conn.commit()
        return {"id": cursor.lastrowid, "msg": "Jogador criado!"}
    except sqlite3.IntegrityError:
        return {"erro": "Esse nick já existe!"}
    finally:
        conn.close()

# Listar todos (GET)
@app.get("/jogadores")
def listar_jogadores():
    conn = conectar()
    jogadores = conn.execute("SELECT * FROM jogadores").fetchall()
    conn.close()
    return [dict(j) for j in jogadores]

# Ver um jogador pelo nick (GET)
@app.get("/jogadores/{nick}")
def ver_jogador(nick: str):
    conn = conectar()
    jogador = conn.execute(
        "SELECT * FROM jogadores WHERE nick = ?", (nick,)
    ).fetchone()
    conn.close()
    if jogador:
        return dict(jogador)
    return {"erro": "Jogador não encontrado!"}

# Atualizar pontos/level (PUT) - útil quando o jogador sobe de nível!
@app.put("/jogadores/{nick}")
def atualizar_jogador(nick: str, jogador: Jogador):
    conn = conectar()
    conn.execute(
        "UPDATE jogadores SET pontos = ?, level = ? WHERE nick = ?",
        (jogador.pontos, jogador.level, nick)
    )
    conn.commit()
    conn.close()
    return {"msg": "Atualizado!"}

# Deletar (DELETE)
@app.delete("/jogadores/{nick}")
def deletar_jogador(nick: str):
    conn = conectar()
    conn.execute("DELETE FROM jogadores WHERE nick = ?", (nick,))
    conn.commit()
    conn.close()
    return {"msg": "Deletado!"}
    
    class Renomear(BaseModel):
    novo_nick: str

# Alterar o nick de um jogador
@app.patch("/jogadores/{nick}")
def renomear(nick: str, data: Renomear):
    conn = conectar()
    try:
        conn.execute(
            "UPDATE jogadores SET nick = ? WHERE nick = ?",
            (data.novo_nick, nick)
        )
        conn.commit()
        return {"msg": f"Nick alterado para {data.novo_nick}!"}
    except sqlite3.IntegrityError:
        return {"erro": "Esse nick já está em uso!"}
    finally:
        conn.close()

# Página de teste
@app.get("/teste")
def pagina_teste():
    return FileResponse("teste.html")