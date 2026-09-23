"""
chat.py — Sistema de chat com moderação para a API Game (Postgres/Neon).

✅ Autenticação igual ao resto da API: cod_token + nick
✅ Cargos vêm de usuarios.nivel_acesso: 0 = user | 1 = mod | 2 = admin
✅ Comandos (/) NUNCA aparecem no chat — só o resultado vai pro executor
✅ /promover @nick mod|admin|usuario (só admin)
✅ /banir @nick  → mod: 1 hora | admin: permanente
✅ /removermsgs N, /cooldown N (admin), /pm @nick msg, /grito msg
✅ Tags [mod] e [admin] antes do nick nas mensagens (campo 'cargo')
"""
import re
import time

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

import users

router = APIRouter(prefix="/chat", tags=["chat"])

CARGOS = {0: "user", 1: "mod", 2: "admin"}
NIVEIS = {"user": 0, "usuario": 0, "mod": 1, "admin": 2}  # "usuario" = apelido pt


# ══════════════════════════════════════
#  HELPERS
# ══════════════════════════════════════
def conectar():
    return users.get_conn()


def init_chat():
    """Cria as tabelas do chat e migra as colunas de moderação em usuarios."""
    conn = conectar()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS mensagens (
            id           SERIAL PRIMARY KEY,
            nick         TEXT NOT NULL,
            cargo        TEXT DEFAULT 'user',
            texto        TEXT NOT NULL,
            tipo         TEXT DEFAULT 'normal',
            destinatario TEXT,
            removida     INTEGER DEFAULT 0,
            ts           DOUBLE PRECISION
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS config (
            chave TEXT PRIMARY KEY,
            valor TEXT
        )
    """)
    conn.execute(
        "INSERT INTO config (chave, valor) VALUES ('cooldown', '30') "
        "ON CONFLICT (chave) DO NOTHING"
    )
    # Migrações: colunas de moderação na tabela usuarios
    conn.execute("ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS ban_ate DOUBLE PRECISION DEFAULT 0")
    conn.execute("ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS ban_perm INTEGER DEFAULT 0")
    conn.execute("ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS ultima_msg DOUBLE PRECISION DEFAULT 0")
    conn.execute("ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS ultimo_grito DOUBLE PRECISION DEFAULT 0")
    conn.close()


def autenticar(cod_token: str, nick: str) -> dict:
    """Valida cod_token + nick e retorna o usuário com dados de moderação."""
    u = users.buscar_usuario(cod_token, nick)
    if not u:
        raise HTTPException(401, "cod_token ou nick inválidos.")
    with conectar() as conn:
        cur = conn.execute(
            "SELECT ban_ate, ban_perm, ultima_msg, ultimo_grito FROM usuarios WHERE id = %s",
            (u["id"],),
        )
        row = cur.fetchone()
    u.update({
        "ban_ate": row[0] or 0,
        "ban_perm": row[1] or 0,
        "ultima_msg": row[2] or 0,
        "ultimo_grito": row[3] or 0,
    })
    return u


def cargo_de(nivel_acesso: int) -> str:
    return CARGOS.get(nivel_acesso, "user")


def get_cooldown() -> int:
    with conectar() as conn:
        cur = conn.execute("SELECT valor FROM config WHERE chave = 'cooldown'")
        row = cur.fetchone()
    return int(row[0]) if row else 30


def _inserir_sistema(conn, texto: str, ts: float):
    """Mensagem de sistema (aviso pra todos), sem poluir com o comando."""
    conn.execute(
        """INSERT INTO mensagens (nick, cargo, texto, tipo, ts)
           VALUES ('⚙️ Sistema', 'admin', %s, 'sistema', %s)""",
        (texto, ts),
    )


def _buscar_usuario_por_nick(conn, nick: str):
    cur = conn.execute(
        "SELECT id, nick, nivel_acesso FROM usuarios WHERE nick = %s AND status = TRUE",
        (nick,),
    )
    row = cur.fetchone()
    if not row:
        return None
    return {"id": row[0], "nick": row[1], "nivel_acesso": row[2]}


# ══════════════════════════════════════
#  MODELOS
# ══════════════════════════════════════
class MensagemEnvio(BaseModel):
    cod_token: str
    nick: str
    texto: str


# ══════════════════════════════════════
#  ROTAS
# ══════════════════════════════════════
@router.post("/enviar")
def enviar_mensagem(dados: MensagemEnvio):
    agora = time.time()
    conn = conectar()
    user = autenticar(dados.cod_token, dados.nick)

    # --- 1. CHECA BANIMENTO ---
    if user["ban_perm"]:
        conn.close()
        raise HTTPException(403, "Você está banido PERMANENTEMENTE do chat!")
    if user["ban_ate"] and user["ban_ate"] > agora:
        restante = int(user["ban_ate"] - agora)
        conn.close()
        raise HTTPException(403, f"Você está banido por mais {restante // 60}min {restante % 60}s")
    if user["ban_ate"] and user["ban_ate"] <= agora:
        conn.execute("UPDATE usuarios SET ban_ate = 0 WHERE id = %s", (user["id"],))

    texto = dados.texto.strip()
    if not texto:
        conn.close()
        raise HTTPException(400, "Mensagem vazia!")

    cargo = cargo_de(user["nivel_acesso"])

    # --- 2. COMANDOS (NUNCA entram no histórico do chat) ---
    cmd_banir = re.match(r"^/banir\s+@(\S+)$", texto)
    cmd_remover = re.match(r"^/removermsgs\s+(\d+)$", texto)
    cmd_pm = re.match(r"^/pm\s+@(\S+)\s+(.+)$", texto, re.DOTALL)
    cmd_grito = re.match(r"^/grito\s+(.+)$", texto, re.DOTALL)
    cmd_cooldown = re.match(r"^/(?:coldown|cooldown)\s+(\d+)$", texto)
    cmd_promover = re.match(r"^/promover\s+@(\S+)\s+(\S+)$", texto)

    # 👑 /promover @nick mod|admin|usuario → SÓ ADMIN
    if cmd_promover:
        if cargo != "admin":
            conn.close()
            raise HTTPException(403, "Só admin pode promover!")
        alvo_nick = cmd_promover.group(1)
        novo_cargo = cmd_promover.group(2).lower()
        if novo_cargo not in NIVEIS:
            conn.close()
            raise HTTPException(400, "Cargo inválido! Use: mod, admin ou usuario")
        alvo = _buscar_usuario_por_nick(conn, alvo_nick)
        if not alvo:
            conn.close()
            raise HTTPException(404, "Jogador não encontrado!")
        if alvo["nivel_acesso"] == 2 and NIVEIS[novo_cargo] != 2:
            conn.close()
            raise HTTPException(403, "Não pode rebaixar outro admin!")
        conn.execute(
            "UPDATE usuarios SET nivel_acesso = %s WHERE id = %s",
            (NIVEIS[novo_cargo], alvo["id"]),
        )
        _inserir_sistema(conn, f"👑 {alvo_nick} agora é {NIVEIS[novo_cargo] if novo_cargo != 'usuario' else 'user'}!", agora)
        conn.close()
        return {"msg": f"👑 {alvo_nick} promovido!", "comando": True}

    # 👢 /banir @nick → mod: 1 hora | admin: permanente
    if cmd_banir:
        if cargo not in ("mod", "admin"):
            conn.close()
            raise HTTPException(403, "Só mods e admins podem banir!")
        alvo_nick = cmd_banir.group(1)
        alvo = _buscar_usuario_por_nick(conn, alvo_nick)
        if not alvo:
            conn.close()
            raise HTTPException(404, "Jogador não encontrado!")
        if alvo["nivel_acesso"] == 2:
            conn.close()
            raise HTTPException(403, "Não pode banir um admin!")
        if cargo == "mod" and alvo["nivel_acesso"] == 1:
            conn.close()
            raise HTTPException(403, "Mod não pode banir outro mod!")
        if cargo == "admin":
            conn.execute("UPDATE usuarios SET ban_perm = 1 WHERE id = %s", (alvo["id"],))
            aviso = f"🔨 {alvo_nick} foi banido PERMANENTEMENTE do chat!"
        else:
            conn.execute("UPDATE usuarios SET ban_ate = %s WHERE id = %s", (agora + 3600, alvo["id"]))
            aviso = f"🔨 {alvo_nick} foi banido por 1 hora!"
        _inserir_sistema(conn, aviso, agora)
        conn.close()
        return {"msg": aviso, "comando": True}

    # 🧹 /removermsgs N → mod+: remove as últimas N mensagens
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
                ORDER BY id DESC LIMIT %s
            )
        """, (n,))
        _inserir_sistema(conn, f"🧹 {user['nick']} removeu as últimas {n} mensagens.", agora)
        conn.close()
        return {"msg": f"🧹 Últimas {n} mensagens removidas!", "comando": True}

    # ⏱ /cooldown N → admin: muda o cooldown global
    if cmd_cooldown:
        if cargo != "admin":
            conn.close()
            raise HTTPException(403, "Só admin pode mudar o cooldown!")
        valor = cmd_cooldown.group(1)
        conn.execute("UPDATE config SET valor = %s WHERE chave = 'cooldown'", (valor,))
        conn.close()
        return {"msg": f"⏱ Cooldown do chat alterado para {valor}s!", "comando": True}

    # --- 3. COOLDOWN DE MENSAGEM (admin ignora) ---
    cooldown = get_cooldown()
    if cargo != "admin":
        restante = cooldown - (agora - user["ultima_msg"])
        if restante > 0:
            conn.close()
            raise HTTPException(429, f"Aguarde {int(restante) + 1}s para enviar outra mensagem!")

    # 💬 /pm @nick mensagem → só destinatário e remetente veem
    if cmd_pm:
        destinatario = cmd_pm.group(1)
        pm_texto = cmd_pm.group(2)
        if not _buscar_usuario_por_nick(conn, destinatario):
            conn.close()
            raise HTTPException(404, "Destinatário não encontrado!")
        conn.execute(
            """INSERT INTO mensagens (nick, cargo, texto, tipo, destinatario, ts)
               VALUES (%s, %s, %s, 'pm', %s, %s)""",
            (user["nick"], cargo, pm_texto, destinatario, agora),
        )
        conn.execute("UPDATE usuarios SET ultima_msg = %s WHERE id = %s", (agora, user["id"]))
        conn.close()
        return {"msg": f"💬 PM enviada para {destinatario}!", "comando": True}

    # 📢 /grito "msg" → destacado, 1x a cada 5 minutos
    if cmd_grito:
        grito_texto = cmd_grito.group(1).strip('"')
        restante_grito = 300 - (agora - user["ultimo_grito"])
        if restante_grito > 0:
            conn.close()
            raise HTTPException(429, f"Grito em cooldown! Aguarde {int(restante_grito) + 1}s.")
        conn.execute(
            """INSERT INTO mensagens (nick, cargo, texto, tipo, ts)
               VALUES (%s, %s, %s, 'grito', %s)""",
            (user["nick"], cargo, grito_texto, agora),
        )
        conn.execute(
            "UPDATE usuarios SET ultima_msg = %s, ultimo_grito = %s WHERE id = %s",
            (agora, agora, user["id"]),
        )
        conn.close()
        return {"msg": "📢 Grito enviado!", "comando": True}

    # --- 4. MENSAGEM NORMAL ---
    conn.execute(
        """INSERT INTO mensagens (nick, cargo, texto, tipo, ts)
           VALUES (%s, %s, %s, 'normal', %s)""",
        (user["nick"], cargo, texto, agora),
    )
    conn.execute("UPDATE usuarios SET ultima_msg = %s WHERE id = %s", (agora, user["id"]))
    conn.close()
    return {"msg": "Enviado!"}


@router.get("/mensagens")
def ler_mensagens(apos_id: int = 0, cod_token: str = "", nick: str = ""):
    """Polling: retorna mensagens novas visíveis para este jogador.
    PMs só aparecem para remetente e destinatário."""
    user = autenticar(cod_token, nick)
    conn = conectar()
    cur = conn.execute("""
        SELECT id, nick, cargo, texto, tipo, destinatario, ts
        FROM mensagens
        WHERE id > %s AND removida = 0
          AND (tipo != 'pm' OR destinatario = %s OR nick = %s)
        ORDER BY id ASC
        LIMIT 200
    """, (apos_id, user["nick"], user["nick"]))
    rows = cur.fetchall()
    conn.close()
    return [
        {"id": r[0], "nick": r[1], "cargo": r[2], "texto": r[3],
         "tipo": r[4], "destinatario": r[5], "ts": r[6]}
        for r in rows
    ]
