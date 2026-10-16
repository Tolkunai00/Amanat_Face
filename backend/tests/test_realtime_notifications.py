import asyncio

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from school_face_id.config.security import (
    SESSION_COOKIE, create_session, delete_session, token_digest,
)
from school_face_id.config.settings import ALLOWED_ORIGINS
from school_face_id.db.connection import get_connection
from school_face_id.main import app
from school_face_id.realtime.manager import ConnectionManager
from school_face_id.repositories import students
from school_face_id.services import attendance as attendance_service
from school_face_id.services import notifications as notification_service
from school_face_id.services import parents as parent_service


WS_PATH = "/api/v1/ws/notifications"
ALLOWED_ORIGIN = ALLOWED_ORIGINS[0]


def make_parent_with_child(login):
    child = students.save_student(f"{login} child", "Realtime Class", "", None)
    parent = parent_service.create_parent(login, login, None, f"{login}-secret")
    parent_service.link_parent_to_student(parent["id"], child["id"], "guardian")
    return parent, child


def websocket_headers(token, origin=ALLOWED_ORIGIN):
    return {"origin": origin, "cookie": f"{SESSION_COOKIE}={token}"}


def test_anonymous_invalid_and_unknown_origin_websockets_are_rejected():
    client = TestClient(app)
    with pytest.raises(WebSocketDisconnect) as anonymous:
        with client.websocket_connect(WS_PATH, headers={"origin": ALLOWED_ORIGIN}):
            pass
    assert anonymous.value.code == 4401

    with pytest.raises(WebSocketDisconnect) as invalid:
        with client.websocket_connect(
            WS_PATH, headers=websocket_headers("invalid-session-token")
        ):
            pass
    assert invalid.value.code == 4401

    expired_token = create_session(
        "parent", login="expired-parent", parent_id="expired-parent-id"
    )
    conn = get_connection()
    conn.execute(
        "UPDATE auth_sessions SET expires_at=? WHERE token_hash=?",
        (0, token_digest(expired_token)),
    )
    conn.commit()
    conn.close()
    with pytest.raises(WebSocketDisconnect) as expired:
        with client.websocket_connect(
            WS_PATH, headers=websocket_headers(expired_token)
        ):
            pass
    assert expired.value.code == 4401

    token = create_session("parent", login="origin-parent", parent_id="origin-parent-id")
    try:
        with pytest.raises(WebSocketDisconnect) as wrong_origin:
            with client.websocket_connect(
                WS_PATH, headers=websocket_headers(token, "https://attacker.example")
            ):
                pass
        assert wrong_origin.value.code == 4403
    finally:
        delete_session(token)


def test_authenticated_parent_connects_and_receives_persisted_notification():
    parent, child = make_parent_with_child("realtime-parent")
    token = create_session("parent", login=parent["login"], parent_id=parent["id"])
    client = TestClient(app)
    try:
        with client.websocket_connect(WS_PATH, headers=websocket_headers(token)) as websocket:
            assert websocket.receive_json() == {"type": "connected"}
            result = attendance_service.register_event(
                child["id"], "enter", debounce_seconds=0
            )
            payload = websocket.receive_json()
            assert result["status"] == "created"
            assert payload["type"] == "notification.created"
            assert payload["data"]["student"]["id"] == child["id"]
            assert "recipient_id" not in payload["data"]
            assert "photo_path" not in str(payload)
    finally:
        delete_session(token)


def test_one_parent_receives_on_two_simultaneous_connections():
    parent, child = make_parent_with_child("realtime-two-tabs")
    token = create_session("parent", login=parent["login"], parent_id=parent["id"])
    client = TestClient(app)
    try:
        with client.websocket_connect(WS_PATH, headers=websocket_headers(token)) as first:
            with client.websocket_connect(WS_PATH, headers=websocket_headers(token)) as second:
                assert first.receive_json()["type"] == "connected"
                assert second.receive_json()["type"] == "connected"
                attendance_service.register_event(child["id"], "exit", debounce_seconds=0)
                assert first.receive_json()["type"] == "notification.created"
                assert second.receive_json()["type"] == "notification.created"
    finally:
        delete_session(token)


class FakeWebSocket:
    def __init__(self, fail=False):
        self.fail = fail
        self.accepted = False
        self.messages = []

    async def accept(self):
        self.accepted = True

    async def send_json(self, payload):
        if self.fail:
            raise RuntimeError("socket closed")
        self.messages.append(payload)


def test_manager_routes_by_role_and_recipient_and_removes_dead_sockets():
    async def scenario():
        manager = ConnectionManager()
        parent_a_first = FakeWebSocket()
        parent_a_second = FakeWebSocket()
        parent_b = FakeWebSocket()
        teacher_a = FakeWebSocket()
        teacher_b_dead = FakeWebSocket(fail=True)
        await manager.connect("parent", "parent-a", parent_a_first)
        await manager.connect("parent", "parent-a", parent_a_second)
        await manager.connect("parent", "parent-b", parent_b)
        await manager.connect("teacher", "teacher-a", teacher_a)
        await manager.connect("teacher", "teacher-b", teacher_b_dead)

        parent_payload = {"type": "notification.created", "data": {"id": "parent-event"}}
        teacher_payload = {"type": "notification.created", "data": {"id": "teacher-event"}}
        assert await manager.send_to_recipient("parent", "parent-a", parent_payload) == 2
        assert await manager.send_to_recipient("teacher", "teacher-a", teacher_payload) == 1
        assert await manager.send_to_recipient("teacher", "teacher-b", teacher_payload) == 0

        assert parent_a_first.messages == parent_a_second.messages == [parent_payload]
        assert parent_b.messages == []
        assert teacher_a.messages == [teacher_payload]
        assert await manager.connection_count("teacher", "teacher-b") == 0
        await manager.disconnect("parent", "parent-a", parent_a_first)
        assert await manager.connection_count("parent", "parent-a") == 1

    asyncio.run(scenario())


def test_idempotent_notification_creation_publishes_only_once(monkeypatch):
    parent, child = make_parent_with_child("realtime-idempotent")
    published = []

    from school_face_id.realtime import publisher

    monkeypatch.setattr(publisher, "publish_notification", lambda row: published.append(row["id"]))
    event = attendance_service.register_event(child["id"], "enter", debounce_seconds=0)["event"]
    assert len(published) == 1
    assert notification_service.create_for_attendance_event(event["id"]) == []
    assert len(published) == 1


def test_offline_notification_remains_available_through_http():
    parent, child = make_parent_with_child("realtime-offline")
    attendance_service.register_event(child["id"], "enter", debounce_seconds=0)
    token = create_session(
        "parent", login=parent["login"], parent_id=parent["id"], csrf_token="offline-csrf"
    )
    client = TestClient(app)
    client.cookies.set(SESSION_COOKIE, token)
    try:
        response = client.get("/api/v1/notifications")
        assert response.status_code == 200
        assert len(response.json()) == 1
        assert response.json()[0]["student"]["id"] == child["id"]
    finally:
        delete_session(token)
