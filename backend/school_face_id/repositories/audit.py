"""Append-only administrative audit history."""

import json
import secrets

from ..db.connection import get_connection


def record_admin_action(actor_login, action, entity_type, entity_id=None, details=None):
    conn = get_connection()
    conn.execute(
        "INSERT INTO audit_log "
        "(id, actor_login, action, entity_type, entity_id, details_json) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (
            secrets.token_hex(16), actor_login, action, entity_type, entity_id,
            json.dumps(details or {}, ensure_ascii=False, separators=(",", ":")),
        ),
    )
    conn.commit()
    conn.close()


def list_admin_actions(limit=100, actor_login=None, entity_type=None):
    conditions = []
    params = []
    if actor_login:
        conditions.append("actor_login = ?")
        params.append(actor_login)
    if entity_type:
        conditions.append("entity_type = ?")
        params.append(entity_type)
    where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
    params.append(min(max(int(limit), 1), 500))
    conn = get_connection()
    rows = conn.execute(
        "SELECT id, actor_login, action, entity_type, entity_id, details_json, created_at "
        f"FROM audit_log{where} ORDER BY created_at DESC, id DESC LIMIT ?",
        tuple(params),
    ).fetchall()
    conn.close()
    result = []
    for row in rows:
        try:
            details = json.loads(row[5])
        except (TypeError, ValueError):
            details = {}
        result.append({
            "id": row[0], "actor_login": row[1], "action": row[2],
            "entity_type": row[3], "entity_id": row[4], "details": details,
            "created_at": row[6],
        })
    return result
