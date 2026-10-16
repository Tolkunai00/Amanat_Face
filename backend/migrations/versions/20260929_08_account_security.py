"""Harden managed accounts, teacher assignments, classes, and audit history.

Revision ID: 20260929_08
Revises: 20260928_07
"""

from alembic import op
import sqlalchemy as sa


revision = "20260929_08"
down_revision = "20260928_07"
branch_labels = None
depends_on = None


def _timestamp_default(dialect):
    if dialect == "postgresql":
        return sa.text(
            "to_char(CURRENT_TIMESTAMP AT TIME ZONE 'Asia/Almaty', "
            "'YYYY-MM-DD\"T\"HH24:MI:SS') || '+05:00'"
        )
    return sa.text("(strftime('%Y-%m-%dT%H:%M:%S','now','+5 hours') || '+05:00')")


def upgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name

    op.add_column(
        "teachers", sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true())
    )
    op.add_column(
        "parent_accounts",
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.add_column(
        "classes", sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true())
    )
    op.execute("CREATE UNIQUE INDEX uq_teachers_login_lower ON teachers (lower(login))")

    op.create_table(
        "teacher_classes_v3",
        sa.Column("teacher_id", sa.Text(), nullable=False),
        sa.Column("class_id", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["teacher_id"], ["teachers.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["class_id"], ["classes.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("teacher_id", "class_id"),
    )
    bind.execute(
        sa.text(
            "INSERT INTO teacher_classes_v3 (teacher_id, class_id) "
            "SELECT t.id, tc.class_id FROM teacher_classes tc "
            "JOIN teachers t ON t.login = tc.teacher_login"
        )
    )
    op.drop_table("teacher_classes")
    op.rename_table("teacher_classes_v3", "teacher_classes")
    op.create_index("idx_teacher_classes_class", "teacher_classes", ["class_id"])

    op.create_table(
        "audit_log",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("actor_login", sa.Text(), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("entity_type", sa.Text(), nullable=False),
        sa.Column("entity_id", sa.Text(), nullable=True),
        sa.Column("details_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column(
            "created_at", sa.Text(), nullable=False, server_default=_timestamp_default(dialect)
        ),
    )
    op.create_index("idx_audit_log_created", "audit_log", ["created_at"])
    op.create_index("idx_audit_log_actor", "audit_log", ["actor_login", "created_at"])

    if dialect == "postgresql":
        op.execute(
            """
            CREATE FUNCTION prevent_cross_role_login() RETURNS trigger AS $$
            BEGIN
              IF TG_TABLE_NAME = 'teachers' AND EXISTS (
                SELECT 1 FROM parent_accounts WHERE lower(login) = lower(NEW.login)
              ) THEN RAISE EXCEPTION 'login is already used by a parent'; END IF;
              IF TG_TABLE_NAME = 'parent_accounts' AND EXISTS (
                SELECT 1 FROM teachers WHERE lower(login) = lower(NEW.login)
              ) THEN RAISE EXCEPTION 'login is already used by a teacher'; END IF;
              RETURN NEW;
            END; $$ LANGUAGE plpgsql
            """
        )
        op.execute(
            "CREATE TRIGGER teachers_cross_role_login BEFORE INSERT OR UPDATE OF login ON teachers "
            "FOR EACH ROW EXECUTE FUNCTION prevent_cross_role_login()"
        )
        op.execute(
            "CREATE TRIGGER parents_cross_role_login BEFORE INSERT OR UPDATE OF login ON parent_accounts "
            "FOR EACH ROW EXECUTE FUNCTION prevent_cross_role_login()"
        )
    else:
        op.execute(
            "CREATE TRIGGER teachers_cross_role_login_insert BEFORE INSERT ON teachers "
            "WHEN EXISTS (SELECT 1 FROM parent_accounts WHERE lower(login)=lower(NEW.login)) "
            "BEGIN SELECT RAISE(ABORT, 'login is already used by a parent'); END"
        )
        op.execute(
            "CREATE TRIGGER teachers_cross_role_login_update BEFORE UPDATE OF login ON teachers "
            "WHEN EXISTS (SELECT 1 FROM parent_accounts WHERE lower(login)=lower(NEW.login)) "
            "BEGIN SELECT RAISE(ABORT, 'login is already used by a parent'); END"
        )
        op.execute(
            "CREATE TRIGGER parents_cross_role_login_insert BEFORE INSERT ON parent_accounts "
            "WHEN EXISTS (SELECT 1 FROM teachers WHERE lower(login)=lower(NEW.login)) "
            "BEGIN SELECT RAISE(ABORT, 'login is already used by a teacher'); END"
        )
        op.execute(
            "CREATE TRIGGER parents_cross_role_login_update BEFORE UPDATE OF login ON parent_accounts "
            "WHEN EXISTS (SELECT 1 FROM teachers WHERE lower(login)=lower(NEW.login)) "
            "BEGIN SELECT RAISE(ABORT, 'login is already used by a teacher'); END"
        )


def downgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name
    if dialect == "postgresql":
        op.execute("DROP TRIGGER parents_cross_role_login ON parent_accounts")
        op.execute("DROP TRIGGER teachers_cross_role_login ON teachers")
        op.execute("DROP FUNCTION prevent_cross_role_login()")
    else:
        for trigger in (
            "parents_cross_role_login_update", "parents_cross_role_login_insert",
            "teachers_cross_role_login_update", "teachers_cross_role_login_insert",
        ):
            op.execute(f"DROP TRIGGER {trigger}")

    op.drop_index("idx_audit_log_actor", table_name="audit_log")
    op.drop_index("idx_audit_log_created", table_name="audit_log")
    op.drop_table("audit_log")

    op.create_table(
        "teacher_classes_v2",
        sa.Column("teacher_login", sa.Text(), nullable=False),
        sa.Column("class_id", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["teacher_login"], ["teachers.login"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["class_id"], ["classes.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("teacher_login", "class_id"),
    )
    bind.execute(
        sa.text(
            "INSERT INTO teacher_classes_v2 (teacher_login, class_id) "
            "SELECT t.login, tc.class_id FROM teacher_classes tc "
            "JOIN teachers t ON t.id = tc.teacher_id"
        )
    )
    op.drop_index("idx_teacher_classes_class", table_name="teacher_classes")
    op.drop_table("teacher_classes")
    op.rename_table("teacher_classes_v2", "teacher_classes")
    op.drop_index("uq_teachers_login_lower", table_name="teachers")
    op.drop_column("classes", "is_active")
    op.drop_column("parent_accounts", "is_active")
    op.drop_column("teachers", "is_active")
