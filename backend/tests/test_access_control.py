from fastapi.testclient import TestClient

from school_face_id.config.security import SESSION_COOKIE, create_session, delete_session
from school_face_id.main import app


def client_for(role=None, **claims):
    client = TestClient(app)
    token = None
    if role:
        claims.setdefault("csrf_token", "test-csrf-token")
        token = create_session(role, login=claims.pop("login", role), **claims)
        client.cookies.set(SESSION_COOKIE, token)
        client.headers["X-CSRF-Token"] = claims["csrf_token"]
    return client, token


def close_session(token):
    if token:
        delete_session(token)


def test_only_non_sensitive_routes_are_public():
    client = TestClient(app)
    assert client.get("/").status_code == 200
    assert client.get("/api/v1/health").status_code == 200
    assert client.get("/api/v1/auth/me").json() == {"authenticated": False}


def test_personal_routes_reject_anonymous_requests():
    client = TestClient(app)
    checks = [
        ("GET", "/api/v1/students"),
        ("GET", "/api/v1/students/Any%20Student/photo"),
        ("GET", "/api/v1/attendance/classes"),
        ("GET", "/api/v1/attendance/recent"),
        ("GET", "/api/v1/attendance/parent"),
        ("GET", "/api/v1/teachers"),
        ("GET", "/api/v1/notifications"),
        ("GET", "/api/v1/notifications/unread-count"),
        ("GET", "/api/v1/camera/feed"),
    ]
    for method, path in checks:
        response = client.request(method, path)
        assert response.status_code == 401, (method, path, response.text)


def test_parent_can_only_open_own_report(monkeypatch):
    from school_face_id.api.routers import attendance

    monkeypatch.setattr(attendance, "get_parent_daily_report_by_id", lambda parent_id: [])
    client, token = client_for(
        "parent",
        parent_id="parent-test-id",
    )
    try:
        assert client.get("/api/v1/attendance/parent").status_code == 200
        assert client.get("/api/v1/attendance/classes").status_code == 403
        assert client.get("/api/v1/students").status_code == 403
        assert client.get("/api/v1/teachers").status_code == 403
        assert client.get("/api/v1/camera/feed").status_code == 403
    finally:
        close_session(token)


def test_teacher_is_limited_to_assigned_classes(monkeypatch):
    from school_face_id.api.routers import attendance

    monkeypatch.setattr(attendance, "get_class_daily_report", lambda class_name: [])
    client, token = client_for("teacher", classes=["7A"])
    try:
        assert client.get("/api/v1/attendance/classes/7A").status_code == 200
        assert client.get("/api/v1/attendance/classes/8B").status_code == 403
        assert client.get("/api/v1/attendance/recent").status_code == 403
        assert client.get("/api/v1/students").status_code == 403
        assert client.get("/api/v1/teachers").status_code == 403
        assert client.get("/api/v1/camera/feed").status_code == 403
    finally:
        close_session(token)


def test_admin_can_list_protected_data(monkeypatch):
    from school_face_id.api.routers import attendance, students, teachers

    monkeypatch.setattr(students.student_service, "list_students", lambda: [])
    monkeypatch.setattr(attendance, "get_recent_attendance", lambda limit: [])
    monkeypatch.setattr(teachers, "get_teachers", lambda: [])
    client, token = client_for("admin")
    try:
        assert client.get("/api/v1/students").status_code == 200
        assert client.get("/api/v1/attendance/recent").status_code == 200
        assert client.get("/api/v1/teachers").status_code == 200
    finally:
        close_session(token)


def test_legacy_public_media_routes_do_not_exist():
    paths = {route.path for route in app.routes}
    assert "/face_database" not in paths
    assert "/screenshots" not in paths
    assert "/static" not in paths


def test_empty_admin_secret_never_allows_login(monkeypatch):
    from school_face_id.api.routers import auth

    monkeypatch.setattr(auth, "ADMIN_PASSWORD", "")
    client = TestClient(app)
    response = client.post("/api/v1/auth/login", json={"login": "admin", "password": ""})
    assert response.status_code == 401


def test_mutation_requires_matching_csrf_token(monkeypatch):
    from school_face_id.api.routers import teachers

    monkeypatch.setattr(teachers, "get_teachers", lambda: [])
    token = create_session("admin", login="csrf-admin", csrf_token="expected-token")
    client = TestClient(app)
    client.cookies.set(SESSION_COOKIE, token)
    try:
        rejected = client.post("/api/v1/auth/logout")
        assert rejected.status_code == 403
        assert rejected.json()["detail"] == "Invalid CSRF token"
        accepted = client.post("/api/v1/auth/logout", headers={"X-CSRF-Token": "expected-token"})
        assert accepted.status_code == 204
    finally:
        close_session(token)


def test_login_rate_limit_blocks_repeated_failures():
    client = TestClient(app)
    payload = {"login": "rate-limit-unique-user", "password": "wrong"}
    for _ in range(5):
        assert client.post("/api/v1/auth/login", json=payload).status_code == 401
    assert client.post("/api/v1/auth/login", json=payload).status_code == 429
