"""
users.py — Configuração do banco (Neon Postgres) e todas as funções de usuário.
Toda requisição autenticada envia cod_token + nick, que são validados aqui.
"""
import os
import secrets
from datetime import datetime

import psycopg

# Neon fornece a connection string no dashboard (use a "-pooler" para serverless)
DATABASE_URL = os.getenv("DATABASE_URL")  # ex: postgresql://user:senha@host-pooler/db?sslmode=require


def get_conn():
    conn = psycopg.connect(DATABASE_URL)
    conn.autocommit = True
    return conn


def init_db():
    """Cria as tabelas caso não existam."""
    with get_conn() as conn:
        conn.execute("""
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
        );
        """)
        conn.execute("""
        CREATE TABLE IF NOT EXISTS reward_log (
            id          BIGSERIAL PRIMARY KEY,
            user_id     BIGINT REFERENCES usuarios(id),
            tipo        TEXT NOT NULL,
            recompensa  JSONB NOT NULL,
            data_claim  TIMESTAMP DEFAULT NOW()
        );
        """)


def gerar_cod_token():
    """Token único usado para login / recuperação de conta."""
    return secrets.token_hex(8)  # 16 caracteres hex


def existe_nick(nick: str) -> bool:
    with get_conn() as conn:
        cur = conn.execute("SELECT 1 FROM usuarios WHERE nick = %s", (nick,))
        return cur.fetchone() is not None


def criar_usuario(nick: str) -> dict:
    cod = gerar_cod_token()
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO usuarios (cod_token, nick) VALUES (%s, %s) RETURNING id, cod_token, nick",
            (cod, nick),
        )
        row = cur.fetchone()
    return {"id": row[0], "cod_token": row[1], "nick": row[2]}


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


_COLS = "id, cod_token, nick, pontos, level, exp, gold, diamantes, nivel_acesso, status, data_create"


def buscar_usuario(cod_token: str, nick: str):
    """Validação central: só retorna o usuário se cod_token E nick baterem."""
    with get_conn() as conn:
        cur = conn.execute(
            f"SELECT {_COLS} FROM usuarios WHERE cod_token = %s AND nick = %s AND status = TRUE",
            (cod_token, nick),
        )
        row = cur.fetchone()
    return _row_to_dict(row) if row else None


def adicionar_progresso(cod_token: str, nick: str, pontos=0, exp=0, gold=0, diamantes=0):
    """Soma pontos/exp/gold/diamantes e sobe o level se necessário (100 exp por level)."""
    user = buscar_usuario(cod_token, nick)
    if not user:
        return None

    nova_exp = user["exp"] + exp
    novo_level = user["level"]
    while nova_exp >= novo_level * 100:          # cada level exige level*100 de exp
        nova_exp -= novo_level * 100
        novo_level += 1

    with get_conn() as conn:
        conn.execute(
            """UPDATE usuarios
               SET pontos = pontos + %s, exp = %s, level = %s,
                   gold = gold + %s, diamantes = diamantes + %s
               WHERE id = %s""",
            (pontos, nova_exp, novo_level, gold, diamantes, user["id"]),
        )
    return buscar_usuario(cod_token, nick)
