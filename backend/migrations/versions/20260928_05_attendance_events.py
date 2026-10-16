"""Add immutable attendance events and backfill the legacy attendance table.

Revision ID: 20260928_05
Revises: 20260928_04
"""

from datetime import datetime
import secrets
from zoneinfo import ZoneInfo

from alembic import op
import sqlalchemy as sa


revision = "20260928_05"
down_revision = "20260928_04"
branch_labels = None
depends_on = None

PROJECT_TIMEZONE = ZoneInfo("Asia/Bishkek")


def _local_timestamp(date_value, time_value=None):
    raw = str(time_value if time_value is not None else date_value or "").strip()
    if not raw:
        return None
    if time_value is not None and not (len(raw) >= 10 and raw[4:5] == "-" and raw[7:8] == "-"):
        raw = f"{date_value} {raw}"
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise RuntimeError(f"Cannot parse legacy attendance timestamp: {raw!r}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=PROJECT_TIMEZONE)
    else:
        parsed = parsed.astimezone(PROJECT_TIMEZONE)
    return parsed.isoformat(timespec="seconds")


def upgrade() -> None:
    bind = op.get_bind()
    timestamp_default = (
        sa.text(
            "to_char(CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Bishkek', "
            "'YYYY-MM-DD\"T\"HH24:MI:SS') || '+06:00'"
        )
        if bind.dialect.name == "postgresql"
        else sa.text("(strftime('%Y-%m-%dT%H:%M:%S','now','+6 hours') || '+06:00')")
    )
    op.create_table(
        "attendance_events",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("student_id", sa.Text(), nullable=False),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("occurred_at", sa.Text(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False, server_default="import"),
        sa.Column("photo_path", sa.Text(), nullable=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("created_at", sa.Text(), nullable=False, server_default=timestamp_default),
        sa.ForeignKeyConstraint(["student_id"], ["students.id"], ondelete="CASCADE"),
    )
    op.create_index("idx_attendance_events_student", "attendance_events", ["student_id"])
    op.create_index("idx_attendance_events_occurred", "attendance_events", ["occurred_at"])
    op.create_index(
        "idx_attendance_events_student_occurred",
        "attendance_events",
        ["student_id", "occurred_at"],
    )

    inspector = sa.inspect(bind)
    if "attendance" not in inspector.get_table_names():
        return
    columns = {column["name"] for column in inspector.get_columns("attendance")}
    events = []
    if {"student_id", "status", "timestamp"}.issubset(columns):
        rows = bind.execute(
            sa.text("SELECT student_id, status, timestamp FROM attendance ORDER BY id")
        ).fetchall()
        for student_id, status, timestamp in rows:
            event_type = {"keldi": "enter", "ketti": "exit", "enter": "enter", "exit": "exit"}.get(status)
            if student_id and event_type and timestamp:
                events.append((student_id, event_type, _local_timestamp(timestamp)))
    elif {"student_id", "date"}.issubset(columns):
        selected = ["student_id", "date"]
        if "arrived_at" in columns:
            selected.append("arrived_at")
        if "left_at" in columns:
            selected.append("left_at")
        rows = bind.execute(sa.text(f"SELECT {', '.join(selected)} FROM attendance")).fetchall()
        arrived_index = selected.index("arrived_at") if "arrived_at" in selected else None
        left_index = selected.index("left_at") if "left_at" in selected else None
        for row in rows:
            if arrived_index is not None and row[arrived_index]:
                events.append((row[0], "enter", _local_timestamp(row[1], row[arrived_index])))
            if left_index is not None and row[left_index]:
                events.append((row[0], "exit", _local_timestamp(row[1], row[left_index])))
    else:
        raise RuntimeError(f"Unsupported legacy attendance schema: {sorted(columns)}")

    for student_id, event_type, occurred_at in events:
        bind.execute(
            sa.text(
                "INSERT INTO attendance_events "
                "(id, student_id, event_type, occurred_at, source) "
                "VALUES (:id, :student_id, :event_type, :occurred_at, 'import')"
            ),
            {"id": secrets.token_hex(16), "student_id": student_id,
             "event_type": event_type, "occurred_at": occurred_at},
        )
    migrated = bind.execute(sa.text("SELECT COUNT(*) FROM attendance_events")).scalar_one()
    if migrated != len(events):
        raise RuntimeError(f"Attendance backfill mismatch: expected {len(events)}, migrated {migrated}")


def downgrade() -> None:
    op.drop_index("idx_attendance_events_student_occurred", table_name="attendance_events")
    op.drop_index("idx_attendance_events_occurred", table_name="attendance_events")
    op.drop_index("idx_attendance_events_student", table_name="attendance_events")
    op.drop_table("attendance_events")
