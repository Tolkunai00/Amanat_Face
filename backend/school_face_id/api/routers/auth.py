import hashlib
import secrets
import time

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel

from ...config.security import SESSION_COOKIE, create_session, delete_session, get_session
from ...config.settings import ADMIN_PASSWORD, PRODUCTION
from ...repositories.auth import (
    authenticate_parent,
    authenticate_teacher_identity,
    clear_auth_failures,
    get_auth_rate_limit,
    record_auth_failure,
)
from ...repositories.parents import get_parent_by_id

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    login: str
    password: str


def public_session(session: dict) -> dict:
    return {
        "role": session["role"],
        "login": session.get("login", ""),
        "classes": session.get("classes", []),
        "teacher_id": session.get("teacher_id", "") if session["role"] == "teacher" else "",
        "csrf_token": session.get("csrf_token", ""),
    }


@router.post("/login")
def login(payload: LoginRequest, request: Request, response: Response):
    login_value = payload.login.strip()
    password = payload.password.strip()
    role = ""
    client_host = request.client.host if request.client else "unknown"
    rate_key = hashlib.sha256(f"{client_host}|{login_value.casefold()}".encode("utf-8")).hexdigest()
    now = int(time.time())
    rate = get_auth_rate_limit(rate_key)
    if rate and rate[2] > now:
        raise HTTPException(status_code=429, detail="Too many login attempts")
    claims: dict = {"login": login_value, "csrf_token": secrets.token_urlsafe(32)}

    if ADMIN_PASSWORD and login_value.lower() == "admin" and secrets.compare_digest(password, ADMIN_PASSWORD):
        role = "admin"
    else:
        teacher = authenticate_teacher_identity(login_value, password)
        if teacher is not None:
            role = "teacher"
            claims["login"] = teacher["login"]
            claims["classes"] = teacher["classes"]
            claims["teacher_id"] = teacher["teacher_id"]
        else:
            parent_id = authenticate_parent(login_value, password)
            if parent_id:
                role = "parent"
                claims["parent_id"] = parent_id
                parent = get_parent_by_id(parent_id)
                if parent is not None:
                    claims["login"] = parent["login"]

    if not role:
        record_auth_failure(rate_key, now)
        response.status_code = 401
        return {"detail": "Неверный логин или пароль"}

    clear_auth_failures(rate_key)
    token = create_session(role, **claims)
    response.set_cookie(
        SESSION_COOKIE,
        token,
        httponly=True,
        secure=PRODUCTION,
        samesite="lax",
        max_age=12 * 60 * 60,
        path="/",
    )
    return public_session({"role": role, **claims})


@router.post("/logout", status_code=204)
def logout(request: Request, response: Response):
    delete_session(request.cookies.get(SESSION_COOKIE, ""))
    response.delete_cookie(SESSION_COOKIE, path="/")


@router.get("/me")
def me(request: Request):
    session = get_session(request)
    if session is None:
        return {"authenticated": False}
    return {"authenticated": True, **public_session(session)}
