"""Add stable teacher IDs and persistent in-app notifications.

Revision ID: 20260928_06
Revises: 20260928_05
"""

import secrets

from alembic import op
import sqlalchemy as sa


revision = "20260928_06"
down_revision = "20260928_05"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    teacher_columns = {column["name"] for column in sa.inspect(bind).get_columns("teachers")}
    if "id" not in teacher_columns:
        op.add_column("teachers", sa.Column("id", sa.Text(), nullable=True))
    teachers = bind.execute(sa.text("SELECT login FROM teachers WHERE id IS NULL")).fetchall()
    for (login,) in teachers:
        bind.execute(
            sa.text("UPDATE teachers SET id=:teacher_id WHERE login=:login"),
            {"teacher_id": secrets.token_hex(16), "login": login},
        )
    op.create_index("uq_teachers_id", "teachers", ["id"], unique=True)
    if bind.dialect.name == "postgresql":
        op.alter_column("teachers", "id", existing_type=sa.Text(), nullable=False)
    else:
        op.execute(
            "CREATE TRIGGER teachers_id_required_insert BEFORE INSERT ON teachers "
            "WHEN NEW.id IS NULL OR NEW.id = '' BEGIN "
            "SELECT RAISE(ABORT, 'teacher id is required'); END"
        )
        op.execute(
            "CREATE TRIGGER teachers_id_required_update BEFORE UPDATE OF id ON teachers "
            "WHEN NEW.id IS NULL OR NEW.id = '' BEGIN "
            "SELECT RAISE(ABORT, 'teacher id is required'); END"
        )

    timestamp_default = (
        sa.text(
            "to_char(CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Bishkek', "
            "'YYYY-MM-DD\"T\"HH24:MI:SS') || '+06:00'"
        )
        if bind.dialect.name == "postgresql"
        else sa.text("(strftime('%Y-%m-%dT%H:%M:%S','now','+6 hours') || '+06:00')")
    )
    op.create_table(
        "notifications",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("recipient_type", sa.Text(), nullable=False),
        sa.Column("recipient_id", sa.Text(), nullable=False),
        sa.Column("student_id", sa.Text(), nullable=False),
        sa.Column("attendance_event_id", sa.Text(), nullable=False),
        sa.Column("notification_type", sa.Text(), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("channel", sa.Text(), nullable=False, server_default="in_app"),
        sa.Column("is_read", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("read_at", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Text(), nullable=False, server_default=timestamp_default),
        sa.ForeignKeyConstraint(["student_id"], ["students.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["attendance_event_id"], ["attendance_events.id"], ondelete="CASCADE"
        ),
        sa.UniqueConstraint(
            "recipient_type", "recipient_id", "attendance_event_id", "notification_type",
            name="uq_notification_recipient_event_type",
        ),
    )
    op.create_index(
        "idx_notifications_recipient_created", "notifications",
        ["recipient_type", "recipient_id", "created_at"],
    )
    op.create_index(
        "idx_notifications_recipient_read", "notifications",
        ["recipient_type", "recipient_id", "is_read"],
    )
    op.create_index(
        "idx_notifications_attendance_event", "notifications", ["attendance_event_id"]
    )


def downgrade() -> None:
    bind = op.get_bind()
    op.drop_index("idx_notifications_attendance_event", table_name="notifications")
    op.drop_index("idx_notifications_recipient_read", table_name="notifications")
    op.drop_index("idx_notifications_recipient_created", table_name="notifications")
    op.drop_table("notifications")
    if bind.dialect.name != "postgresql":
        op.execute("DROP TRIGGER teachers_id_required_update")
        op.execute("DROP TRIGGER teachers_id_required_insert")
    op.drop_index("uq_teachers_id", table_name="teachers")
    op.drop_column("teachers", "id")
