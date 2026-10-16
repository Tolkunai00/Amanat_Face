"""Add expiration metadata for attendance event screenshots.

Revision ID: 20260928_07
Revises: 20260928_06

Existing photo references are retained and receive the documented legacy
retention value of seven days from the event time.  This migration never
touches the filesystem; cleanup is an explicit runtime operation.
"""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from alembic import op
import sqlalchemy as sa


revision = "20260928_07"
down_revision = "20260928_06"
branch_labels = None
depends_on = None

LOCAL_TIMEZONE = ZoneInfo("Asia/Bishkek")


def _legacy_expiration(value):
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=LOCAL_TIMEZONE)
    else:
        parsed = parsed.astimezone(LOCAL_TIMEZONE)
    return (parsed + timedelta(days=7)).isoformat(timespec="seconds")


def upgrade() -> None:
    op.add_column(
        "attendance_events", sa.Column("photo_expires_at", sa.Text(), nullable=True)
    )
    bind = op.get_bind()
    rows = bind.execute(
        sa.text(
            "SELECT id, occurred_at FROM attendance_events "
            "WHERE photo_path IS NOT NULL AND photo_path <> ''"
        )
    ).fetchall()
    for event_id, occurred_at in rows:
        bind.execute(
            sa.text(
                "UPDATE attendance_events SET photo_expires_at=:expires_at WHERE id=:event_id"
            ),
            {"expires_at": _legacy_expiration(occurred_at), "event_id": event_id},
        )
    op.create_index(
        "idx_attendance_events_photo_expires_at",
        "attendance_events",
        ["photo_expires_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "idx_attendance_events_photo_expires_at", table_name="attendance_events"
    )
    op.drop_column("attendance_events", "photo_expires_at")
