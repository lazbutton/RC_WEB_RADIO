from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import time
from collections import defaultdict
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from regie.kernel import db

ROLES = ("admin", "membre", "invite")
LEVELS = ("none", "read", "write", "admin")
LEVEL_RANK = {level: index for index, level in enumerate(LEVELS)}
MODULES = ("mail", "contacts", "events", "shows", "planning", "calendar", "files", "publish", "radio", "settings")
DEFAULT_PERMISSIONS: dict[str, dict[str, str]] = {
    "admin": {module: "admin" for module in MODULES},
    "membre": {**{module: "write" for module in MODULES}, "settings": "read"},
    "invite": {**{module: "read" for module in MODULES}, "mail": "none", "settings": "none", "files": "none"},
}
LOGIN_WINDOW = 60.0
LOGIN_MAX_FAILS = 8
SCRYPT_N = 2**15
SCRYPT_MAXMEM = 64 * 1024 * 1024


# --- mots de passe (scrypt, bibliothèque standard) -----------------------------------------


def _scrypt(password: str, salt: bytes, dklen: int = 32) -> bytes:
    return hashlib.scrypt(password.encode("utf-8"), salt=salt, n=SCRYPT_N, r=8, p=1, maxmem=SCRYPT_MAXMEM, dklen=dklen)


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = _scrypt(password, salt)
    return "scrypt$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(digest).decode()


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, salt_b64, digest_b64 = stored.split("$", 2)
        if scheme != "scrypt":
            return False
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(digest_b64)
    except Exception:
        return False
    digest = _scrypt(password, salt, dklen=len(expected))
    return hmac.compare_digest(digest, expected)


# --- secrets chiffrés (AES-GCM, clé dérivée de REGIE_SECRET) ---------------------------------


class SecretBox:
    def __init__(self, master: str) -> None:
        if not master:
            raise ValueError("REGIE_SECRET manquant")
        self.key = hashlib.sha256(("regie-secrets:" + master).encode("utf-8")).digest()

    def encrypt(self, plaintext: str, aad: str = "") -> bytes:
        nonce = os.urandom(12)
        return nonce + AESGCM(self.key).encrypt(nonce, plaintext.encode("utf-8"), aad.encode("utf-8"))

    def decrypt(self, blob: bytes, aad: str = "") -> str:
        data = bytes(blob)
        return AESGCM(self.key).decrypt(data[:12], data[12:], aad.encode("utf-8")).decode("utf-8")


class Secrets:
    def __init__(self, dsn: str, org_id: str, box: SecretBox) -> None:
        self.dsn = dsn
        self.org_id = org_id
        self.box = box

    def put(self, scope: str, key: str, value: str) -> None:
        blob = self.box.encrypt(value, aad=f"{scope}:{key}")
        with db.connect(self.dsn) as conn:
            db.execute(
                conn,
                "INSERT INTO secrets (org_id, scope, key, ciphertext) VALUES (%s, %s, %s, %s) ON CONFLICT (org_id, scope, key) DO UPDATE SET ciphertext = EXCLUDED.ciphertext, updated_at = now()",
                (self.org_id, scope, key, blob),
            )

    def get(self, scope: str, key: str) -> str:
        with db.connect(self.dsn) as conn:
            row = db.fetch_one(conn, "SELECT ciphertext FROM secrets WHERE org_id = %s AND scope = %s AND key = %s", (self.org_id, scope, key))
        if not row:
            return ""
        return self.box.decrypt(row["ciphertext"], aad=f"{scope}:{key}")

    def delete(self, scope: str, key: str) -> None:
        with db.connect(self.dsn) as conn:
            db.execute(conn, "DELETE FROM secrets WHERE org_id = %s AND scope = %s AND key = %s", (self.org_id, scope, key))

    def keys(self, scope: str) -> list[str]:
        with db.connect(self.dsn) as conn:
            rows = db.fetch_all(conn, "SELECT key FROM secrets WHERE org_id = %s AND scope = %s ORDER BY key", (self.org_id, scope))
        return [str(r["key"]) for r in rows]


# --- comptes, sessions, permissions -----------------------------------------------------------


def public_user(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": str(row["id"]),
        "email": row["email"],
        "name": row.get("name") or "",
        "role": row.get("role") or "membre",
        "prefs": row.get("prefs") or {},
        "disabled": bool(row.get("disabled_at")),
        "last_login_at": db.iso(row.get("last_login_at")),
        "created_at": db.iso(row.get("created_at")),
    }


class Auth:
    def __init__(self, dsn: str, org_id: str, session_days: int = 30) -> None:
        self.dsn = dsn
        self.org_id = org_id
        self.session_days = session_days
        self._fails: dict[str, list[float]] = defaultdict(list)

    # utilisateurs

    def create_user(self, email: str, name: str, password: str, role: str = "membre") -> dict[str, Any]:
        if role not in ROLES:
            raise ValueError("rôle inconnu")
        if len(password) < 10:
            raise ValueError("mot de passe trop court (10 caractères minimum)")
        with db.connect(self.dsn) as conn:
            row = db.fetch_one(
                conn,
                "INSERT INTO users (org_id, email, name, password_hash, role) VALUES (%s, %s, %s, %s, %s) RETURNING *",
                (self.org_id, email.strip().lower(), name.strip(), hash_password(password), role),
            )
        return public_user(row or {})

    def users(self) -> list[dict[str, Any]]:
        with db.connect(self.dsn) as conn:
            rows = db.fetch_all(conn, "SELECT * FROM users WHERE org_id = %s ORDER BY created_at", (self.org_id,))
        return [public_user(r) for r in rows]

    def user(self, user_id: str) -> dict[str, Any] | None:
        with db.connect(self.dsn) as conn:
            row = db.fetch_one(conn, "SELECT * FROM users WHERE id = %s", (user_id,))
        return public_user(row) if row else None

    def user_by_email(self, email: str) -> dict[str, Any] | None:
        with db.connect(self.dsn) as conn:
            row = db.fetch_one(conn, "SELECT * FROM users WHERE org_id = %s AND lower(email) = lower(%s)", (self.org_id, email.strip()))
        return dict(row) if row else None

    def count(self) -> int:
        with db.connect(self.dsn) as conn:
            return int(db.scalar(conn, "SELECT COUNT(*) FROM users WHERE org_id = %s", (self.org_id,)) or 0)

    def update_user(self, user_id: str, *, name: str | None = None, role: str | None = None, disabled: bool | None = None, prefs: dict | None = None, password: str | None = None) -> dict[str, Any] | None:
        sets: list[str] = []
        args: list[Any] = []
        if name is not None:
            sets.append("name = %s")
            args.append(name.strip())
        if role is not None:
            if role not in ROLES:
                raise ValueError("rôle inconnu")
            sets.append("role = %s")
            args.append(role)
        if disabled is not None:
            sets.append("disabled_at = CASE WHEN %s THEN now() ELSE NULL END")
            args.append(disabled)
        if prefs is not None:
            sets.append("prefs = prefs || %s")
            args.append(db.J(prefs))
        if password is not None:
            if len(password) < 10:
                raise ValueError("mot de passe trop court (10 caractères minimum)")
            sets.append("password_hash = %s")
            args.append(hash_password(password))
        if not sets:
            return self.user(user_id)
        args.append(user_id)
        with db.connect(self.dsn) as conn:
            row = db.fetch_one(conn, f"UPDATE users SET {', '.join(sets)} WHERE id = %s RETURNING *", args)
            if disabled:
                db.execute(conn, "DELETE FROM sessions WHERE user_id = %s", (user_id,))
        return public_user(row) if row else None

    # connexion

    def blocked(self, ip: str) -> bool:
        now = time.monotonic()
        hits = [t for t in self._fails[ip] if now - t < LOGIN_WINDOW]
        self._fails[ip] = hits
        return len(hits) >= LOGIN_MAX_FAILS

    def _event(self, kind: str, user_id: str | None, ip: str, detail: str = "") -> None:
        with db.connect(self.dsn) as conn:
            db.execute(conn, "INSERT INTO auth_events (org_id, user_id, kind, ip, detail) VALUES (%s, %s, %s, %s, %s)", (self.org_id, user_id, kind, ip, detail[:200]))

    def login(self, email: str, password: str, ip: str = "", user_agent: str = "") -> tuple[str, dict[str, Any]] | None:
        if self.blocked(ip):
            raise PermissionError("Trop d'essais. Réessaie dans une minute.")
        row = self.user_by_email(email)
        ok = bool(row) and not row.get("disabled_at") and verify_password(password, str(row.get("password_hash") or ""))
        if not ok:
            self._fails[ip].append(time.monotonic())
            self._event("login_failed", str(row["id"]) if row else None, ip, email[:80])
            return None
        assert row is not None
        self._fails.pop(ip, None)
        token = secrets.token_urlsafe(32)
        with db.connect(self.dsn) as conn:
            db.execute(
                conn,
                "INSERT INTO sessions (token_hash, user_id, expires_at, ip, user_agent) VALUES (%s, %s, now() + make_interval(days => %s), %s, %s)",
                (self._hash(token), row["id"], self.session_days, ip, user_agent[:200]),
            )
            db.execute(conn, "UPDATE users SET last_login_at = now() WHERE id = %s", (row["id"],))
        self._event("login", str(row["id"]), ip)
        return token, public_user(row)

    def logout(self, token: str) -> None:
        with db.connect(self.dsn) as conn:
            db.execute(conn, "DELETE FROM sessions WHERE token_hash = %s", (self._hash(token),))

    def resolve(self, token: str) -> dict[str, Any] | None:
        if not token:
            return None
        with db.connect(self.dsn) as conn:
            row = db.fetch_one(
                conn,
                """
                SELECT u.* FROM sessions s JOIN users u ON u.id = s.user_id
                WHERE s.token_hash = %s AND s.expires_at > now() AND u.disabled_at IS NULL
                """,
                (self._hash(token),),
            )
            if row:
                db.execute(conn, "UPDATE sessions SET last_seen_at = now() WHERE token_hash = %s AND last_seen_at < now() - interval '5 minutes'", (self._hash(token),))
        return public_user(row) if row else None

    def _hash(self, token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def prune_sessions(self) -> int:
        with db.connect(self.dsn) as conn:
            return db.execute(conn, "DELETE FROM sessions WHERE expires_at < now()")

    # permissions

    def ensure_default_permissions(self) -> None:
        with db.connect(self.dsn) as conn:
            for role, modules in DEFAULT_PERMISSIONS.items():
                for module, level in modules.items():
                    db.execute(
                        conn,
                        "INSERT INTO permissions (org_id, role, module, level) VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING",
                        (self.org_id, role, module, level),
                    )

    def matrix(self) -> dict[str, dict[str, str]]:
        with db.connect(self.dsn) as conn:
            rows = db.fetch_all(conn, "SELECT role, module, level FROM permissions WHERE org_id = %s", (self.org_id,))
        out: dict[str, dict[str, str]] = {role: dict(DEFAULT_PERMISSIONS[role]) for role in ROLES}
        for row in rows:
            out.setdefault(row["role"], {})[row["module"]] = row["level"]
        return out

    def set_permission(self, role: str, module: str, level: str) -> None:
        if role not in ROLES or level not in LEVELS:
            raise ValueError("rôle ou niveau inconnu")
        with db.connect(self.dsn) as conn:
            db.execute(
                conn,
                "INSERT INTO permissions (org_id, role, module, level) VALUES (%s, %s, %s, %s) ON CONFLICT (org_id, role, module) DO UPDATE SET level = EXCLUDED.level",
                (self.org_id, role, module, level),
            )

    def level(self, role: str, module: str) -> str:
        if role == "admin":
            return "admin"
        return self.matrix().get(role, {}).get(module, "none")

    def allows(self, user: dict[str, Any] | None, module: str, needed: str) -> bool:
        if not user:
            return False
        have = self.level(str(user.get("role") or "invite"), module)
        return LEVEL_RANK[have] >= LEVEL_RANK[needed]
