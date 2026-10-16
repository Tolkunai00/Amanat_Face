from fastapi.testclient import TestClient

from school_face_id.config.security import SESSION_COOKIE, create_session, delete_session
from school_face_id.main import app


def admin_client():
    token = create_session("admin", login="security-admin", csrf_token="security-csrf")
    client = TestClient(app)
    client.cookies.set(SESSION_COOKIE, token)
    client.headers["X-CSRF-Token"] = "security-csrf"
    return client, token


def login(login_value, password):
    client = TestClient(app)
    response = client.post(
        "/api/v1/auth/login", json={"login": login_value, "password": password}
    )
    return client, response


def test_teacher_lifecycle_uses_ids_revokes_sessions_and_blocks_instead_of_deleting():
    admin, admin_token = admin_client()
    try:
        first_class = admin.post("/api/v1/classes", json={"name": "Security 12A"})
        second_class = admin.post("/api/v1/classes", json={"name": "Security 12B"})
        assert first_class.status_code == second_class.status_code == 201
        first_id = first_class.json()["id"]
        second_id = second_class.json()["id"]

        weak = admin.post(
            "/api/v1/teachers",
            json={"login": "security-teacher", "password": "short", "class_ids": [first_id]},
        )
        assert weak.status_code == 400

        created = admin.post(
            "/api/v1/teachers",
            json={
                "login": "security-teacher", "password": "teacher-secret-123",
                "class_ids": [first_id],
            },
        )
        assert created.status_code == 201
        teacher_id = created.json()["id"]

        duplicate_parent = admin.post(
            "/api/v1/parents",
            json={
                "login": "SECURITY-TEACHER", "display_name": "Conflict Parent",
                "password": "parent-secret-123",
            },
        )
        assert duplicate_parent.status_code == 409

        teacher_client, signed_in = login("SECURITY-TEACHER", "teacher-secret-123")
        assert signed_in.status_code == 200
        assert signed_in.json()["login"] == "security-teacher"
        updated = admin.put(
            f"/api/v1/teachers/{teacher_id}",
            json={"login": "security-teacher-new", "class_ids": [second_id]},
        )
        assert updated.status_code == 200
        assert updated.json()["id"] == teacher_id
        assert updated.json()["class_ids"] == [second_id]
        assert teacher_client.get("/api/v1/auth/me").json() == {"authenticated": False}

        teacher_client, signed_in = login("security-teacher-new", "teacher-secret-123")
        assert signed_in.status_code == 200
        assert signed_in.json()["teacher_id"] == teacher_id
        assert signed_in.json()["classes"] == ["Security 12B"]

        weak_change = admin.put(
            f"/api/v1/teachers/{teacher_id}/password", json={"new_password": "tiny"}
        )
        assert weak_change.status_code == 400
        changed = admin.put(
            f"/api/v1/teachers/{teacher_id}/password",
            json={"new_password": "new-teacher-secret-123"},
        )
        assert changed.status_code == 200
        assert teacher_client.get("/api/v1/auth/me").json() == {"authenticated": False}
        assert login("security-teacher-new", "teacher-secret-123")[1].status_code == 401
        assert login("security-teacher-new", "new-teacher-secret-123")[1].status_code == 200

        blocked = admin.delete(f"/api/v1/teachers/{teacher_id}")
        assert blocked.status_code == 200
        assert blocked.json()["status"] == "blocked"
        listed = admin.get("/api/v1/teachers").json()
        account = next(item for item in listed if item["id"] == teacher_id)
        assert account["is_active"] is False
        assert login("security-teacher-new", "new-teacher-secret-123")[1].status_code == 401
    finally:
        delete_session(admin_token)


def test_classes_have_independent_api_and_admin_mutations_are_audited():
    admin, admin_token = admin_client()
    try:
        created = admin.post("/api/v1/classes", json={"name": "Audit Empty Class"})
        assert created.status_code == 201
        class_id = created.json()["id"]
        catalog = admin.get("/api/v1/classes").json()
        assert any(item["id"] == class_id and item["name"] == "Audit Empty Class" for item in catalog)

        renamed = admin.put(
            f"/api/v1/classes/{class_id}", json={"name": "Audit Renamed Class"}
        )
        assert renamed.status_code == 200
        blocked = admin.delete(f"/api/v1/classes/{class_id}")
        assert blocked.status_code == 200
        inactive = admin.get("/api/v1/classes?include_inactive=true").json()
        assert next(item for item in inactive if item["id"] == class_id)["is_active"] is False

        audit = admin.get("/api/v1/audit?entity_type=classes&limit=20")
        assert audit.status_code == 200
        paths = {item["details"]["path"] for item in audit.json()}
        assert "/api/v1/classes" in paths
        assert f"/api/v1/classes/{class_id}" in paths
        assert all("password" not in str(item["details"]).lower() for item in audit.json())
    finally:
        delete_session(admin_token)
