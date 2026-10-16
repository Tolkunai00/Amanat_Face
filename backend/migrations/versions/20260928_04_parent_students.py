"""Normalize parent profiles and add the parent/student many-to-many relation.

Revision ID: 20260928_04
Revises: 20260927_03
"""

from alembic import op
import sqlalchemy as sa


revision = "20260928_04"
down_revision = "20260927_03"
branch_labels = None
depends_on = None


def _columns(bind, table: str) -> set[str]:
    return {column["name"] for column in sa.inspect(bind).get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    parent_columns = _columns(bind, "parent_accounts")
    if "display_name" not in parent_columns:
        op.add_column("parent_accounts", sa.Column("display_name", sa.Text(), nullable=True))
        bind.execute(sa.text("UPDATE parent_accounts SET display_name = login"))
        op.alter_column("parent_accounts", "display_name", existing_type=sa.Text(), nullable=False)
    else:
        bind.execute(
            sa.text(
                "UPDATE parent_accounts SET display_name = login "
                "WHERE display_name IS NULL OR trim(display_name) = ''"
            )
        )

    if "phone" not in parent_columns:
        op.add_column("parent_accounts", sa.Column("phone", sa.Text(), nullable=True))

    timestamp_default = (
        sa.text("to_char(CURRENT_TIMESTAMP, 'YYYY-MM-DD HH24:MI:SS')")
        if bind.dialect.name == "postgresql"
        else sa.text("(datetime('now'))")
    )
    op.create_table(
        "parent_students",
        sa.Column("parent_id", sa.Text(), nullable=False),
        sa.Column("student_id", sa.Text(), nullable=False),
        sa.Column("relationship", sa.Text(), nullable=False, server_default="guardian"),
        sa.Column("created_at", sa.Text(), nullable=False, server_default=timestamp_default),
        sa.ForeignKeyConstraint(["parent_id"], ["parent_accounts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["student_id"], ["students.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("parent_id", "student_id"),
    )
    op.create_index("idx_parent_students_student", "parent_students", ["student_id"])

    student_columns = _columns(bind, "students")
    if "parent_id" in student_columns:
        bind.execute(
            sa.text(
                "INSERT INTO parent_students (parent_id, student_id, relationship) "
                "SELECT parent_id, id, 'guardian' FROM students "
                "WHERE parent_id IS NOT NULL ON CONFLICT DO NOTHING"
            )
        )
        legacy_count = bind.execute(
            sa.text("SELECT COUNT(*) FROM students WHERE parent_id IS NOT NULL")
        ).scalar_one()
        migrated_count = bind.execute(
            sa.text(
                "SELECT COUNT(*) FROM parent_students ps JOIN students s ON s.id = ps.student_id "
                "WHERE s.parent_id IS NOT NULL AND ps.parent_id = s.parent_id"
            )
        ).scalar_one()
        if legacy_count != migrated_count:
            raise RuntimeError(
                f"Parent relation backfill mismatch: {legacy_count} legacy, {migrated_count} migrated"
            )

    # The legacy column is intentionally retained for one release. Dropping it
    # rebuilds the SQLite students table and risks its inbound attendance/face
    # FKs. Application code uses parent_students exclusively after this revision.


def downgrade() -> None:
    op.drop_index("idx_parent_students_student", table_name="parent_students")
    op.drop_table("parent_students")
    op.drop_column("parent_accounts", "phone")
