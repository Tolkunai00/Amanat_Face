"""Normalize students, attendance, face embeddings, and teachers.

Revision ID: 20260925_02
Revises: 20260925_01
"""
from alembic import op
import sqlalchemy as sa

revision = "20260925_02"
down_revision = "20260925_01"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    # PostgreSQL receives the final normalized schema in the first revision.
    if bind.dialect.name == "postgresql":
        return
    op.create_table(
        "teachers_v2",
        sa.Column("login", sa.Text(), primary_key=True),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False, server_default=sa.text("(datetime('now'))")),
    )
    op.create_table(
        "students_v2",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("class_id", sa.Text()),
        sa.Column("parent_id", sa.Text()),
        sa.Column("photo_path", sa.Text()),
        sa.Column("created_at", sa.Text(), nullable=False, server_default=sa.text("(datetime('now'))")),
        sa.Column("updated_at", sa.Text(), nullable=False, server_default=sa.text("(datetime('now'))")),
        sa.ForeignKeyConstraint(["class_id"], ["classes.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["parent_id"], ["parent_accounts.id"], ondelete="SET NULL"),
    )
    op.create_table(
        "teacher_classes_v2",
        sa.Column("teacher_login", sa.Text(), nullable=False),
        sa.Column("class_id", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["teacher_login"], ["teachers_v2.login"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["class_id"], ["classes.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("teacher_login", "class_id"),
    )
    op.create_table(
        "attendance_v2",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("student_id", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("timestamp", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["student_id"], ["students_v2.id"], ondelete="CASCADE"),
    )
    op.create_table(
        "face_embeddings_v2",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("student_id", sa.Text(), nullable=False),
        sa.Column("angle_label", sa.Text(), nullable=False, server_default="front"),
        sa.Column("embedding", sa.LargeBinary(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False, server_default=sa.text("(datetime('now'))")),
        sa.ForeignKeyConstraint(["student_id"], ["students_v2.id"], ondelete="CASCADE"),
    )

    bind.exec_driver_sql(
        "INSERT INTO teachers_v2 (login, password_hash) SELECT login, password_hash FROM teachers"
    )
    bind.exec_driver_sql(
        """
        INSERT INTO students_v2 (id, name, class_id, parent_id, photo_path)
        SELECT student_id, name, class_id, parent_id, photo_path
        FROM students WHERE student_id IS NOT NULL AND student_id != ''
        """
    )
    bind.exec_driver_sql(
        """
        INSERT INTO teacher_classes_v2 (teacher_login, class_id)
        SELECT tc.teacher_login, tc.class_id FROM teacher_classes tc
        JOIN teachers_v2 t ON t.login = tc.teacher_login
        JOIN classes c ON c.id = tc.class_id
        """
    )
    bind.exec_driver_sql(
        """
        INSERT INTO attendance_v2 (id, student_id, status, timestamp)
        SELECT a.id, a.student_id, a.status, a.timestamp FROM attendance a
        JOIN students_v2 s ON s.id = a.student_id
        WHERE a.student_id IS NOT NULL AND a.status IS NOT NULL AND a.timestamp IS NOT NULL
        """
    )
    bind.exec_driver_sql(
        """
        INSERT INTO face_embeddings_v2 (id, student_id, angle_label, embedding, created_at)
        SELECT f.id, f.student_id, COALESCE(f.angle_label, 'front'), f.embedding,
               COALESCE(f.created_at, datetime('now'))
        FROM face_embeddings f JOIN students_v2 s ON s.id = f.student_id
        WHERE f.student_id IS NOT NULL AND f.embedding IS NOT NULL
        """
    )
    bind.exec_driver_sql(
        """
        INSERT INTO face_embeddings_v2 (student_id, angle_label, embedding)
        SELECT s.student_id, 'primary', s.embedding FROM students s
        JOIN students_v2 n ON n.id = s.student_id
        WHERE s.embedding IS NOT NULL
        """
    )

    op.drop_table("teacher_classes")
    op.drop_table("attendance")
    op.drop_table("face_embeddings")
    op.drop_table("students")
    op.drop_table("teachers")
    if "parents" in sa.inspect(bind).get_table_names():
        op.drop_table("parents")

    op.rename_table("teachers_v2", "teachers")
    op.rename_table("students_v2", "students")
    op.rename_table("teacher_classes_v2", "teacher_classes")
    op.rename_table("attendance_v2", "attendance")
    op.rename_table("face_embeddings_v2", "face_embeddings")

    op.create_index("idx_students_name", "students", ["name"])
    op.create_index("idx_students_parent", "students", ["parent_id"])
    op.create_index("idx_students_class", "students", ["class_id"])
    op.create_index("idx_attendance_student_time", "attendance", ["student_id", "timestamp", "id"])
    op.create_index("idx_face_embeddings_student", "face_embeddings", ["student_id"])


def downgrade() -> None:
    raise RuntimeError("Downgrade would recreate plaintext-compatible legacy columns; restore a backup instead")
