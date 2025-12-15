"""API-related CLI commands for the SAM Framework.

Provides lightweight helpers to create API users and start the bundled
FastAPI server. Dependencies are imported lazily so the CLI can still be
used in environments without the web stack installed.
"""
from __future__ import annotations

import getpass
import hashlib
import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional

from sam.config.settings import Settings

logger = logging.getLogger(__name__)

# File used for lightweight credential storage. Passwords are salted and
# hashed (PBKDF2) to avoid writing raw secrets to disk.
_API_USER_STORE = Path(Settings.SAM_API_AGENT_ROOT) / "api_users.json"
_PBKDF2_ROUNDS = 390_000


def _ensure_store() -> None:
    """Ensure the directory for the API user store exists."""
    store_dir = _API_USER_STORE.parent
    store_dir.mkdir(parents=True, exist_ok=True)


def _hash_password(password: str, salt: Optional[bytes] = None) -> Dict[str, str]:
    """Hash a password using PBKDF2-HMAC with SHA-256."""
    import secrets

    salt_bytes = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt_bytes, _PBKDF2_ROUNDS)
    return {
        "salt": salt_bytes.hex(),
        "hash": digest.hex(),
        "algorithm": "pbkdf2_sha256",
        "iterations": _PBKDF2_ROUNDS,
    }


def _load_users() -> Dict[str, Any]:
    """Load the API user registry from disk."""
    if not _API_USER_STORE.exists():
        return {"users": []}
    try:
        with _API_USER_STORE.open("r", encoding="utf-8") as fp:
            return json.load(fp)
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("Falling back to empty API user store: %s", exc)
        return {"users": []}


def _save_users(payload: Dict[str, Any]) -> None:
    """Persist the API user registry to disk."""
    with _API_USER_STORE.open("w", encoding="utf-8") as fp:
        json.dump(payload, fp, indent=2)


def create_api_user(username: str, password: Optional[str] = None, is_admin: bool = False) -> Dict[str, Any]:
    """Create an API user with a salted+hashed password.

    The credentials are persisted to ``SAM_API_AGENT_ROOT/api_users.json``.
    If ``password`` is not provided, the user is prompted interactively.
    """
    if not username:
        raise ValueError("Username must be provided")

    resolved_password = password or getpass.getpass(prompt="Password for API user: ")
    if not resolved_password:
        raise ValueError("Password cannot be empty")

    _ensure_store()
    users = _load_users()
    if any(user.get("username") == username for user in users.get("users", [])):
        raise ValueError(f"API user '{username}' already exists")

    hashed = _hash_password(resolved_password)
    record = {
        "username": username,
        "is_admin": bool(is_admin),
        "password": hashed,
    }
    users.setdefault("users", []).append(record)
    _save_users(users)
    logger.info("Created API user '%s' (admin=%s)", username, is_admin)
    return record


def run_api_server(host: str, port: int, reload: bool = False, log_level: str = "info") -> int:
    """Run the FastAPI server using uvicorn.

    Imports ``fastapi`` and ``uvicorn`` lazily to avoid hard dependencies
    when the CLI is used without the API feature. Returns the uvicorn exit
    code (0 for success).
    """
    try:
        from fastapi import FastAPI
        import uvicorn
    except Exception as exc:  # pragma: no cover - import guard
        raise RuntimeError(
            "FastAPI/uvicorn są wymagane do uruchomienia API. Zainstaluj je poleceniem `uv sync`."
        ) from exc

    app = FastAPI(title="SAM Framework API", root_path=Settings.SAM_API_ROOT_PATH)

    @app.get("/health")
    async def health() -> Dict[str, str]:  # pragma: no cover - lightweight wrapper
        return {"status": "ok"}

    @app.get("/ready")
    async def ready() -> Dict[str, str]:  # pragma: no cover - lightweight wrapper
        return {"status": "ready"}

    logger.info("Starting API server on %s:%s (reload=%s)", host, port, reload)
    uvicorn.run(
        app,
        host=host,
        port=port,
        reload=reload,
        log_level=log_level,
        access_log=True,
        proxy_headers=True,
        forwarded_allow_ips="*",
    )
    return 0


__all__ = ["create_api_user", "run_api_server"]
