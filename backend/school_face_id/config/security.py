"""Persistent, server-side session tokens for API clients."""

import hashlib
import hmac
import json
import secrets
import time

from fastapi import HTTPException, Request
from starlette.requests import HTTPConnection

from .settings import SESSION_SECRET

from ..repositories.auth import (
    delete_auth_session,
    delete_expired_auth_sessions,
    get_auth_session,
    save_auth_session,
)

SESSION_COOKIE = "school_session"
SESSION_TTL_SECONDS = 12 * 60 * 60


def token_digest(token: str) -> str:
    # A keyed digest makes a stolen sessions table insufficient to validate
    # guessed bearer tokens. Development keeps the historical deterministic
    # digest so isolated tests need no production secret.
    if SESSION_SECRET:
        return hmac.new(
            SESSION_SECRET.encode("utf-8"), token.encode("utf-8"), hashlib.sha256
        ).hexdigest()
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_session(role: str, **claims) -> str:
    token = secrets.token_urlsafe(48)
    now = int(time.time())
    login = str(claims.pop("login", ""))
    delete_expired_auth_sessions(now)
    save_auth_session(
        token_digest(token),
        role,
        login,
        json.dumps(claims, ensure_ascii=False, separators=(",", ":")),
        now,
        now + SESSION_TTL_SECONDS,
    )
    return token


def delete_session(token: str) -> None:
    if token:
        delete_auth_session(token_digest(token))


def get_session(request: HTTPConnection) -> dict | None:
    token = request.cookies.get(SESSION_COOKIE, "")
    if not token:
        return None
    row = get_auth_session(token_digest(token))
    if row is None:
        return None

    role, login, claims_json, created_at, expires_at = row
    if expires_at <= int(time.time()):
        delete_auth_session(token_digest(token))
        return None

    try:
        claims = json.loads(claims_json) if claims_json else {}
    except (TypeError, ValueError):
        delete_auth_session(token_digest(token))
        return None
    return {
        "role": role,
        "login": login,
        "created_at": created_at,
        "expires_at": expires_at,
        **claims,
    }


def require_session(request: Request, *roles: str) -> dict:
    session = get_session(request)
    if session is None:
        raise HTTPException(status_code=401, detail="Authentication required")
    if roles and session["role"] not in roles:
        raise HTTPException(status_code=403, detail="Access denied")
    return session
