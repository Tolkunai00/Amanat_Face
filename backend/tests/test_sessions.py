import sqlite3

from fastapi.testclient import TestClient

from school_face_id.config.security import SESSION_COOKIE, create_session, token_digest
from school_face_id.data import database
from school_face_id.main import app


def test_raw_session_token_is_not_stored_in_database():
    token = create_session("teacher", login="stored-teacher", classes=["7A"])
    conn = sqlite3.connect(database.DB_NAME)
    row = conn.execute(
        "SELECT token_hash, role, login FROM auth_sessions WHERE token_hash = ?",
        (token_digest(token),),
    ).fetchone()
    raw_match = conn.execute(
        "SELECT 1 FROM auth_sessions WHERE token_hash = ?",
        (token,),
    ).fetchone()
    conn.close()
    assert row == (token_digest(token), "teacher", "stored-teacher")
    assert raw_match is None


def test_logout_revokes_server_side_session():
    token = create_session("admin", login="admin", csrf_token="logout-csrf")
    client = TestClient(app)
    client.cookies.set(SESSION_COOKIE, token)
    client.headers["X-CSRF-Token"] = "logout-csrf"
    assert client.get("/api/v1/auth/me").json()["authenticated"] is True
    assert client.post("/api/v1/auth/logout").status_code == 204
    assert client.get("/api/v1/auth/me").json() == {"authenticated": False}


def test_expired_session_is_rejected_and_removed():
    token = create_session("teacher", login="expired", classes=["7A"])
    digest = token_digest(token)
    conn = sqlite3.connect(database.DB_NAME)
    conn.execute("UPDATE auth_sessions SET expires_at = 0 WHERE token_hash = ?", (digest,))
    conn.commit()
    conn.close()

    client = TestClient(app)
    client.cookies.set(SESSION_COOKIE, token)
    assert client.get("/api/v1/auth/me").json() == {"authenticated": False}

    conn = sqlite3.connect(database.DB_NAME)
    assert conn.execute("SELECT 1 FROM auth_sessions WHERE token_hash = ?", (digest,)).fetchone() is None
    conn.close()
