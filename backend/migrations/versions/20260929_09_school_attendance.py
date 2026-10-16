"""Add school schedules, calendar and reasoned attendance corrections.

Revision ID: 20260929_09
Revises: 20260929_08
"""

from alembic import op
import sqlalchemy as sa


revision = "20260929_09"
down_revision = "20260929_08"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "school_shifts",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False, unique=True),
        sa.Column("starts_at", sa.Text(), nullable=False),
        sa.Column("ends_at", sa.Text(), nullable=False),
        sa.Column("late_after", sa.Text(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.create_table(
        "class_schedules",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("class_id", sa.Text(), nullable=False),
        sa.Column("weekday", sa.Integer(), nullable=False),
        sa.Column("shift_id", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["class_id"], ["classes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["shift_id"], ["school_shifts.id"], ondelete="RESTRICT"),
        sa.CheckConstraint("weekday >= 0 AND weekday <= 6", name="ck_schedule_weekday"),
        sa.UniqueConstraint("class_id", "weekday", name="uq_class_schedule_day"),
    )
    op.create_index("idx_class_schedules_class", "class_schedules", ["class_id"])
    op.create_table(
        "school_calendar",
        sa.Column("calendar_date", sa.Text(), primary_key=True),
        sa.Column("is_school_day", sa.Boolean(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False, server_default=""),
    )
    op.create_table(
        "attendance_corrections",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("student_id", sa.Text(), nullable=False),
        sa.Column("attendance_date", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("arrived_at", sa.Text(), nullable=True),
        sa.Column("left_at", sa.Text(), nullable=True),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("actor_login", sa.Text(), nullable=False),
        sa.Column(
            "created_at", sa.Text(), nullable=False,
            server_default=sa.text("CURRENT_TIMESTAMP"),
        ),
        sa.ForeignKeyConstraint(["student_id"], ["students.id"], ondelete="CASCADE"),
        sa.CheckConstraint(
            "status IN ('present','late','absent','excused')",
            name="ck_attendance_correction_status",
        ),
        sa.UniqueConstraint("student_id", "attendance_date", name="uq_student_correction_day"),
    )
    op.create_index(
        "idx_attendance_corrections_date", "attendance_corrections", ["attendance_date"]
    )


def downgrade() -> None:
    op.drop_index("idx_attendance_corrections_date", table_name="attendance_corrections")
    op.drop_table("attendance_corrections")
    op.drop_table("school_calendar")
    op.drop_index("idx_class_schedules_class", table_name="class_schedules")
    op.drop_table("class_schedules")
    op.drop_table("school_shifts")
