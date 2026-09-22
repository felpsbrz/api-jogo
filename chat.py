# chat.py — Sistema de chat com moderação (user >> mod >> admin)
from fastapi import APIRouter, Header, HTTPException, Depends
from pydantic import BaseModel
import sqlite3
import time
import secrets
import hashlib
import re

router = APIRouter(prefix="/chat", tags=["chat"])

DB = "jogo.db"


# ══════════════════════════════════════
#  HELPERS (banco + auth, usados no main)
# ══════════════════════════════════════
def conectar():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    return conn


def _garantir_coluna(conn, coluna, definicao):
    """Adiciona coluna nova se ela ainda não existir (migração simples)."""
    try:
        conn.execute(f"ALTER TABLE jogadores ADD COLUMN {coluna} {definicao}")
        conn.commit()
    except sqlite3.OperationalError:
        pass


def init_chat():
    """Cria as tabelas do chat e migra colunas de moderação."""
    conn = conectar()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS mensagens (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nick TEXT NOT NULL,
            texto TEXT NOT NULL,
            tipo TEXT DEFAULT 'normal',
            destinatario TEXT,
            removida INTEGER DEFAULT 0,
            ts REAL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS config (
            chave TEXT PRIMARY KEY,
            valor TEXT
        )
    """)
    conn.execute(
        "INSERT OR IGNORE INTO config (chave, valor) VALUES ('cooldown', '30')"
    )
    # Migração de colunas de moderação na tabela de jogadores
    _garantir_coluna(conn, "ban_ate", "REAL DEFAULT 0")
    _garantir_coluna(conn, "ban_perm", "INTEGER DEFAULT 0")
    _garantir_coluna(conn, "ultima_msg", "REAL DEFAULT 0")
    _garantir_coluna(conn, "ultimo_grito", "REAL DEFAULT 0")
    conn.commit()
    conn.close()


def gerar_token():
    return secrets.token_hex(16)


def hash_senha(senha: str):
    # Para produção, use bcrypt! SHA-256 é só para demonstração.
    return hashlib.sha256(senha.encode()).hexdigest()


def get_cooldown():
    conn = conectar()
    row = conn.execute(
        "SELECT valor FROM config WHERE chave = 'cooldown'"
    ).fetchone()
    conn.close()
    return int(row["valor"]) if row else 30


def get_jogador_por_token(token: str):
    conn = conectar()
    j = conn.execute(
        "SELECT * FROM jogadores WHERE token = ?", (token,)
    ).fetchone()
    conn.close()
    return dict(j) if j else None


def get_current_user(authorization: str = Header(None)):
    """Dependency: valida o token e retorna o jogador logado."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Token não enviado! Faça login.")
    jogador = get_jogador_por_token(authorization[7:].strip())
    if not jogador:
        raise HTTPException(401, "Token inválido!")
    return jogador


# ══════════════════════════════════════
#  MODELOS
# ══════════════════════════════════════
class MensagemEnvio(BaseModel):
    texto: str


# ══════════════════════════════════════
#  ROTAS DO CHAT
# ══════════════════════════════════════


@router.post("/enviar")
def enviar_mensagem(dados: MensagemEnvio, user=Depends(get_current_user)):
    agora = time.time()
    conn = conectar()

    # --- 1. CHECA BANIMENTO ---
    if user["ban_perm"]:
        conn.close()
        raise HTTPException(403, "Você está banido PERMANENTEMENTE do chat!")
    if user["ban_ate"] and user["ban_ate"] > agora:
        restante = int(user["ban_ate"] - agora)
        conn.close()
        raise HTTPException(
            403,
            f"Você está banido por mais {restante // 60}min {restante % 60}s"
        )
    # Limpa banimento temporário que já expirou
    if user["ban_ate"] and user["ban_ate"] <= agora:
        conn.execute("UPDATE jogadores SET ban_ate = 0 WHERE id = ?",
                     (user["id"],))
        conn.commit()

    texto = dados.texto.strip()
    if not texto:
        conn.close()
        raise HTTPException(400, "Mensagem vazia!")

    cargo = user["cargo"]

    # --- 2. COMANDOS DE MODERAÇÃO ---
    cmd_banir = re.match(r"^/banir\s+@(\S+)$", texto)
    cmd_remover = re.match(r"^/removermsgs\s+(\d+)$", texto)
    cmd_pm = re.match(r"^/pm\s+@(\S+)\s+(.+)$", texto, re.DOTALL)
    cmd_grito = re.match(r"^/grito\s+(.+)$", texto, re.DOTALL)
    cmd_coldown = re.match(r"^/(?:coldown|cooldown)\s+(\d+)$", texto)

    # 👢 /banir @nick  → mod: 1 hora | admin: permanente
    if cmd_banir:
        if cargo not in ("mod", "admin"):
            conn.close()
            raise HTTPException(403, "Só mods e admins podem banir!")
        alvo_nick = cmd_banir.group(1)
        alvo = conn.execute(
            "SELECT * FROM jogadores WHERE nick = ?", (alvo_nick,)
        ).fetchone()
        if not alvo:
            conn.close()
            raise HTTPException(404, "Jogador não encontrado!")
        if alvo["cargo"] == "admin":
            conn.close()
            raise HTTPException(403, "Não pode banir um admin!")
        if cargo == "mod" and alvo["cargo"] == "mod":
            conn.close()
            raise HTTPException(403, "Mod não pode banir outro mod!")

        if cargo == "admin":
            conn.execute(
                "UPDATE jogadores SET ban_perm = 1 WHERE id = ?",
                (alvo["id"],)
            )
            msg = f"🔨 {alvo_nick} foi banido PERMANENTEMENTE do chat!"
        else:
            conn.execute(
                "UPDATE jogadores SET ban_ate = ? WHERE id = ?",
                (agora + 3600, alvo["id"])
            )
            msg = f"🔨 {alvo_nick} foi banido por 1 hora!"
        conn.commit()
        conn.close()
        return {"msg": msg}

    # 🧹 /removermsgs 5  → mod+: remove as últimas N mensagens do chat
    if cmd_remover:
        if cargo not in ("mod", "admin"):
            conn.close()
            raise HTTPException(403, "Só mods e admins podem remover mensagens!")
        n = int(cmd_remover.group(1))
        conn.execute("""
            UPDATE mensagens SET removida = 1
            WHERE id IN (
                SELECT id FROM mensagens
                WHERE tipo != 'pm' AND removida = 0
                ORDER BY id DESC LIMIT ?
            )
        """, (n,))
        conn.commit()
        conn.close()
        return {"msg": f"🧹 Últimas {n} mensagens removidas!"}

    # ⏱ /coldown 60  → admin: muda o cooldown global
    if cmd_coldown:
        if cargo != "admin":
            conn.close()
            raise HTTPException(403, "Só admin pode mudar o cooldown!")
        valor = cmd_coldown.group(1)
        conn.execute(
            "UPDATE config SET valor = ? WHERE chave = 'cooldown'", (valor,)
        )
        conn.commit()
        conn.close()
        return {"msg": f"⏱ Cooldown do chat alterado para {valor}s!"}

    # --- 3. COOLDOWN DE MENSAGEM (admin ignora) ---
    cooldown = get_cooldown()
    if cargo != "admin":
        restante = cooldown - (agora - user["ultima_msg"])
        if restante > 0:
            conn.close()
            raise HTTPException(
                429, f"Aguarde {int(restante) + 1}s para enviar outra mensagem!"
            )

    # 💬 /pm @nick mensagem → só o destinatário (e o remetente) veem
    if cmd_pm:
        destinatario = cmd_pm.group(1)
        pm_texto = cmd_pm.group(2)
        existe = conn.execute(
            "SELECT id FROM jogadores WHERE nick = ?", (destinatario,)
        ).fetchone()
        if not existe:
            conn.close()
            raise HTTPException(404, "Destinatário não encontrado!")
        conn.execute(
            """INSERT INTO mensagens (nick, texto, tipo, destinatario, ts)
               VALUES (?, ?, 'pm', ?, ?)""",
            (user["nick"], pm_texto, destinatario, agora)
        )
        conn.execute("UPDATE jogadores SET ultima_msg = ? WHERE id = ?",
                     (agora, user["id"]))
        conn.commit()
        conn.close()
        return {"msg": f"💬 PM enviada para {destinatario}!"}

    # 📢 /grito "msg" → vermelho e destacado, 1x a cada 5 minutos
    if cmd_grito:
        grito_texto = cmd_grito.group(1).strip('"')
        restante_grito = 300 - (agora - user["ultimo_grito"])
        if restante_grito > 0:
            conn.close()
            raise HTTPException(
                429,
                f"Grito em cooldown! Aguarde {int(restante_grito) + 1}s."
            )
        conn.execute(
            """INSERT INTO mensagens (nick, texto, tipo, ts)
               VALUES (?, ?, 'grito', ?)""",
            (user["nick"], grito_texto, agora)
        )
        conn.execute(
            "UPDATE jogadores SET ultima_msg = ?, ultimo_grito = ? WHERE id = ?",
            (agora, agora, user["id"])
        )
        conn.commit()
        conn.close()
        return {"msg": "📢 Grito enviado!"}

    # --- 4. MENSAGEM NORMAL ---
    conn.execute(
        "INSERT INTO mensagens (nick, texto, tipo, ts) VALUES (?, ?, 'normal', ?)",
        (user["nick"], texto, agora)
    )
    conn.execute("UPDATE jogadores SET ultima_msg = ? WHERE id = ?",
                 (agora, user["id"]))
    conn.commit()
    conn.close()
    return {"msg": "Enviado!"}


@router.get("/mensagens")
def ler_mensagens(apos_id: int = 0, user=Depends(get_current_user)):
    """Polling: retorna mensagens novas visíveis para este jogador.
    PMs só aparecem para remetente e destinatário."""
    conn = conectar()
    rows = conn.execute("""
        SELECT id, nick, texto, tipo, destinatario, ts
        FROM mensagens
        WHERE id > ? AND removida = 0
          AND (tipo != 'pm' OR destinatario = ? OR nick = ?)
        ORDER BY id ASC
        LIMIT 200
    """, (apos_id, user["nick"], user["nick"])).fetchall()
    conn.close()
    return [dict(r) for r in rows]
