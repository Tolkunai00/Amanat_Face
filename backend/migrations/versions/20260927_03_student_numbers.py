"""Add stable unique student numbers and allow duplicate student names.

Revision ID: 20260927_03
Revises: 20260925_02
"""

from alembic import op
import sqlalchemy as sa

revision = "20260927_03"
down_revision = "20260925_02"
branch_labels = None
depends_on = None


def _ordered_student_ids(bind) -> list[str]:
    columns = {column["name"] for column in sa.inspect(bind).get_columns("students")}
    order_by = "created_at, id" if "created_at" in columns else "id"
    return [row[0] for row in bind.exec_driver_sql(f"SELECT id FROM students ORDER BY {order_by}")]


def _drop_name_uniqueness(bind) -> None:
    inspector = sa.inspect(bind)
    for index in inspector.get_indexes("students"):
        if index.get("unique") and index.get("column_names") == ["name"]:
            op.drop_index(index["name"], table_name="students")

    if bind.dialect.name == "postgresql":
        for constraint in inspector.get_unique_constraints("students"):
            if constraint.get("column_names") == ["name"] and constraint.get("name"):
                op.drop_constraint(constraint["name"], "students", type_="unique")


def upgrade() -> None:
    bind = op.get_bind()
    _drop_name_uniqueness(bind)

    if bind.dialect.name == "postgresql":
        op.add_column("students", sa.Column("student_number", sa.Text(), nullable=True))
    else:
        # SQLite cannot add a NOT NULL column to a populated table without a
        # default. Keeping the table in place preserves inbound CASCADE FKs.
        op.add_column(
            "students",
            sa.Column("student_number", sa.Text(), nullable=False, server_default=""),
        )
    student_ids = _ordered_student_ids(bind)
    for number, student_id in enumerate(student_ids, start=1):
        bind.execute(
            sa.text("UPDATE students SET student_number = :student_number WHERE id = :student_id"),
            {"student_number": f"AMANAT-{number:06d}", "student_id": student_id},
        )

    if bind.dialect.name == "postgresql":
        op.execute("CREATE SEQUENCE student_number_seq START WITH 1 INCREMENT BY 1 NO CYCLE")
        bind.execute(
            sa.text("SELECT setval('student_number_seq', :next_value, false)"),
            {"next_value": len(student_ids) + 1},
        )
        op.alter_column("students", "student_number", existing_type=sa.Text(), nullable=False)
    else:
        op.create_table(
            "student_number_counters",
            sa.Column("counter_name", sa.Text(), primary_key=True),
            sa.Column("next_value", sa.BigInteger(), nullable=False),
        )
        bind.execute(
            sa.text(
                "INSERT INTO student_number_counters (counter_name, next_value) "
                "VALUES ('student', :next_value)"
            ),
            {"next_value": len(student_ids) + 1},
        )
        op.execute(
            """
            CREATE TRIGGER students_student_number_required_insert
            BEFORE INSERT ON students
            WHEN NEW.student_number IS NULL OR NEW.student_number = ''
            BEGIN
                SELECT RAISE(ABORT, 'student_number is required');
            END
            """
        )
        op.execute(
            """
            CREATE TRIGGER students_student_number_required_update
            BEFORE UPDATE OF student_number ON students
            WHEN NEW.student_number IS NULL OR NEW.student_number = ''
            BEGIN
                SELECT RAISE(ABORT, 'student_number is required');
            END
            """
        )

    op.create_index(
        "uq_students_student_number",
        "students",
        ["student_number"],
        unique=True,
    )


def downgrade() -> None:
    bind = op.get_bind()
    op.drop_index("uq_students_student_number", table_name="students")
    if bind.dialect.name == "postgresql":
        op.execute("DROP SEQUENCE student_number_seq")
        op.drop_column("students", "student_number")
    else:
        op.execute("DROP TRIGGER students_student_number_required_update")
        op.execute("DROP TRIGGER students_student_number_required_insert")
        op.drop_table("student_number_counters")
        op.drop_column("students", "student_number")
