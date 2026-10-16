"""Create the managed schema and migrate legacy rows in place.

Revision ID: 20260925_01
Revises: None
"""
from __future__ import annotations

import hashlib
import hmac
import secrets

from alembic import op
import sqlalchemy as sa

revision = "20260925_01"
down_revision = None
branch_labels = None
depends_on = None


def _hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 200_000)
    return f"{salt.hex()}:{digest.hex()}"


def _verify_password(password: str, stored_hash: str) -> bool:
    try:
        salt_hex, digest_hex = stored_hash.split(":", 1)
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), 200_000
        )
        return hmac.compare_digest(digest.hex(), digest_hex)
    except (AttributeError, ValueError):
        return False


def _class_id(name: str) -> str:
    return hashlib.sha256(f"class:{name}".encode("utf-8")).hexdigest()[:32]


def _tables(bind) -> set[str]:
    return set(sa.inspect(bind).get_table_names())


def _columns(bind, table: str) -> set[str]:
    return {column["name"] for column in sa.inspect(bind).get_columns(table)}


def _add_column(bind, table: str, column: sa.Column) -> None:
    if column.name not in _columns(bind, table):
        op.add_column(table, column)


def _upgrade_postgresql() -> None:
    timestamp_default = sa.text("to_char(CURRENT_TIMESTAMP, 'YYYY-MM-DD HH24:MI:SS')")
    op.create_table(
        "parent_accounts",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("login", sa.Text(), nullable=False),
        sa.Column("display_name", sa.Text(), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False, server_default=timestamp_default),
    )
    op.create_table(
        "classes",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False, server_default=timestamp_default),
    )
    op.create_table(
        "teachers",
        sa.Column("login", sa.Text(), primary_key=True),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False, server_default=timestamp_default),
    )
    op.create_table(
        "students",
        sa.Column("id", sa.Text(), primary_key=True),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("class_id", sa.Text()),
        sa.Column("parent_id", sa.Text()),
        sa.Column("photo_path", sa.Text()),
        sa.Column("created_at", sa.Text(), nullable=False, server_default=timestamp_default),
        sa.Column("updated_at", sa.Text(), nullable=False, server_default=timestamp_default),
        sa.ForeignKeyConstraint(["class_id"], ["classes.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["parent_id"], ["parent_accounts.id"], ondelete="SET NULL"),
    )
    op.create_table(
        "teacher_classes",
        sa.Column("teacher_login", sa.Text(), nullable=False),
        sa.Column("class_id", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["teacher_login"], ["teachers.login"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["class_id"], ["classes.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("teacher_login", "class_id"),
    )
    op.create_table(
        "attendance",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("student_id", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("timestamp", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["student_id"], ["students.id"], ondelete="CASCADE"),
    )
    op.create_table(
        "face_embeddings",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("student_id", sa.Text(), nullable=False),
        sa.Column("angle_label", sa.Text(), nullable=False, server_default="front"),
        sa.Column("embedding", sa.LargeBinary(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False, server_default=timestamp_default),
        sa.ForeignKeyConstraint(["student_id"], ["students.id"], ondelete="CASCADE"),
    )
    op.create_table(
        "auth_sessions",
        sa.Column("token_hash", sa.Text(), primary_key=True),
        sa.Column("role", sa.Text(), nullable=False),
        sa.Column("login", sa.Text(), nullable=False),
        sa.Column("claims_json", sa.Text(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.BigInteger(), nullable=False),
        sa.Column("expires_at", sa.BigInteger(), nullable=False),
    )
    op.create_table(
        "auth_rate_limits",
        sa.Column("rate_key", sa.Text(), primary_key=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("window_started", sa.BigInteger(), nullable=False),
        sa.Column("blocked_until", sa.BigInteger(), nullable=False, server_default="0"),
    )
    op.execute("CREATE UNIQUE INDEX uq_parent_accounts_login_lower ON parent_accounts (lower(login))")
    op.execute("CREATE UNIQUE INDEX uq_classes_name_lower ON classes (lower(name))")
    op.create_index("idx_students_name", "students", ["name"])
    op.create_index("idx_students_parent", "students", ["parent_id"])
    op.create_index("idx_students_class", "students", ["class_id"])
    op.create_index("idx_attendance_student_time", "attendance", ["student_id", "timestamp", "id"])
    op.create_index("idx_face_embeddings_student", "face_embeddings", ["student_id"])
    op.create_index("idx_auth_sessions_expiry", "auth_sessions", ["expires_at"])


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        _upgrade_postgresql()
        return
    tables = _tables(bind)

    if "parent_accounts" not in tables:
        op.create_table(
            "parent_accounts",
            sa.Column("id", sa.Text(), primary_key=True),
            sa.Column("login", sa.Text(collation="NOCASE"), nullable=False, unique=True),
            sa.Column("display_name", sa.Text(), nullable=False),
            sa.Column("password_hash", sa.Text(), nullable=False),
            sa.Column("created_at", sa.Text(), nullable=False, server_default=sa.text("(datetime('now'))")),
        )
    if "classes" not in tables:
        op.create_table(
            "classes",
            sa.Column("id", sa.Text(), primary_key=True),
            sa.Column("name", sa.Text(collation="NOCASE"), nullable=False, unique=True),
            sa.Column("created_at", sa.Text(), nullable=False, server_default=sa.text("(datetime('now'))")),
        )
    if "teachers" not in tables:
        op.create_table(
            "teachers",
            sa.Column("login", sa.Text(), primary_key=True),
            sa.Column("password_hash", sa.Text(), nullable=False),
            sa.Column("class_names", sa.Text(), nullable=False, server_default=""),
        )
    if "students" not in tables:
        op.create_table(
            "students",
            sa.Column("name", sa.Text(), primary_key=True),
            sa.Column("student_id", sa.Text(), unique=True),
            sa.Column("class_id", sa.Text()),
            sa.Column("class_name", sa.Text()),
            sa.Column("parent_id", sa.Text()),
            sa.Column("photo_path", sa.Text()),
            sa.Column("embedding", sa.LargeBinary()),
            sa.Column("parent_name", sa.Text()),
            sa.Column("parent_code", sa.Text()),
            sa.Column("role", sa.Text(), server_default="student"),
        )
    else:
        _add_column(bind, "students", sa.Column("student_id", sa.Text()))
        _add_column(bind, "students", sa.Column("class_id", sa.Text()))
        _add_column(bind, "students", sa.Column("class_name", sa.Text()))
        _add_column(bind, "students", sa.Column("parent_id", sa.Text()))
        _add_column(bind, "students", sa.Column("photo_path", sa.Text()))
        _add_column(bind, "students", sa.Column("embedding", sa.LargeBinary()))
        _add_column(bind, "students", sa.Column("parent_name", sa.Text()))
        _add_column(bind, "students", sa.Column("parent_code", sa.Text()))
        _add_column(bind, "students", sa.Column("role", sa.Text(), server_default="student"))

    tables = _tables(bind)
    if "attendance" not in tables:
        op.create_table(
            "attendance",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("student_id", sa.Text()),
            sa.Column("name", sa.Text()),
            sa.Column("class_name", sa.Text()),
            sa.Column("status", sa.Text()),
            sa.Column("timestamp", sa.Text()),
        )
    else:
        _add_column(bind, "attendance", sa.Column("student_id", sa.Text()))
    if "parents" not in tables:
        op.create_table(
            "parents",
            sa.Column("code", sa.Text(), primary_key=True),
            sa.Column("name", sa.Text()),
            sa.Column("role", sa.Text(), server_default="parent"),
        )
    if "auth_sessions" not in tables:
        op.create_table(
            "auth_sessions",
            sa.Column("token_hash", sa.Text(), primary_key=True),
            sa.Column("role", sa.Text(), nullable=False),
            sa.Column("login", sa.Text(), nullable=False),
            sa.Column("claims_json", sa.Text(), nullable=False, server_default="{}"),
            sa.Column("created_at", sa.Integer(), nullable=False),
            sa.Column("expires_at", sa.Integer(), nullable=False),
        )
    if "auth_rate_limits" not in tables:
        op.create_table(
            "auth_rate_limits",
            sa.Column("rate_key", sa.Text(), primary_key=True),
            sa.Column("attempts", sa.Integer(), nullable=False),
            sa.Column("window_started", sa.Integer(), nullable=False),
            sa.Column("blocked_until", sa.Integer(), nullable=False, server_default="0"),
        )
    if "face_embeddings" not in tables:
        op.create_table(
            "face_embeddings",
            sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column("student_id", sa.Text()),
            sa.Column("name", sa.Text(), nullable=False),
            sa.Column("angle_label", sa.Text(), server_default="front"),
            sa.Column("embedding", sa.LargeBinary(), nullable=False),
            sa.Column("created_at", sa.Text(), server_default=sa.text("(datetime('now'))")),
        )
    else:
        _add_column(bind, "face_embeddings", sa.Column("student_id", sa.Text()))
    if "teacher_classes" not in tables:
        op.create_table(
            "teacher_classes",
            sa.Column("teacher_login", sa.Text(), nullable=False),
            sa.Column("class_id", sa.Text(), nullable=False),
            sa.ForeignKeyConstraint(["teacher_login"], ["teachers.login"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["class_id"], ["classes.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("teacher_login", "class_id"),
        )

    # Convert legacy parent secrets into PBKDF2 hashes.
    legacy_parents = bind.exec_driver_sql(
        """
        SELECT DISTINCT trim(COALESCE(parent_name, '')), trim(parent_code)
        FROM students
        WHERE parent_code IS NOT NULL AND trim(parent_code) != ''
        """
    ).fetchall()
    for parent_name, plaintext in legacy_parents:
        existing = bind.exec_driver_sql(
            "SELECT id, password_hash FROM parent_accounts WHERE login = ? COLLATE NOCASE",
            (parent_name,),
        ).fetchone() if parent_name else None
        if existing and _verify_password(plaintext, existing[1]):
            parent_id = existing[0]
            login = parent_name
        else:
            parent_id = secrets.token_hex(16)
            base = parent_name or f"parent-{hashlib.sha256(plaintext.encode()).hexdigest()[:8]}"
            login = base
            suffix = hashlib.sha256(plaintext.encode()).hexdigest()[:6]
            counter = 1
            while bind.exec_driver_sql(
                "SELECT 1 FROM parent_accounts WHERE login = ? COLLATE NOCASE", (login,)
            ).fetchone():
                login = f"{base}-{suffix}" if counter == 1 else f"{base}-{suffix}-{counter}"
                counter += 1
            bind.exec_driver_sql(
                "INSERT INTO parent_accounts (id, login, display_name, password_hash) VALUES (?, ?, ?, ?)",
                (parent_id, login, parent_name or login, _hash_password(plaintext)),
            )
        bind.exec_driver_sql(
            """
            UPDATE students SET parent_id = ?, parent_name = ?, parent_code = NULL
            WHERE trim(COALESCE(parent_name, '')) = ? AND parent_code = ?
            """,
            (parent_id, parent_name, parent_name, plaintext),
        )
    bind.exec_driver_sql("DELETE FROM parents")

    students = bind.exec_driver_sql(
        "SELECT rowid, student_id, trim(COALESCE(class_name, '')) FROM students"
    ).fetchall()
    for rowid, student_id, class_name in students:
        student_id = student_id or secrets.token_hex(16)
        class_id = None
        if class_name:
            class_id = _class_id(class_name)
            bind.exec_driver_sql(
                "INSERT OR IGNORE INTO classes (id, name) VALUES (?, ?)", (class_id, class_name)
            )
        bind.exec_driver_sql(
            "UPDATE students SET student_id = ?, class_id = ? WHERE rowid = ?",
            (student_id, class_id, rowid),
        )

    bind.exec_driver_sql(
        """
        UPDATE attendance
        SET student_id = (SELECT s.student_id FROM students s WHERE s.name = attendance.name)
        WHERE student_id IS NULL OR student_id = ''
        """
    )
    bind.exec_driver_sql(
        """
        UPDATE face_embeddings
        SET student_id = (SELECT s.student_id FROM students s WHERE s.name = face_embeddings.name)
        WHERE student_id IS NULL OR student_id = ''
        """
    )

    teachers = bind.exec_driver_sql("SELECT login, class_names FROM teachers").fetchall()
    for login, class_names in teachers:
        for class_name in dict.fromkeys(value.strip() for value in (class_names or "").split(",") if value.strip()):
            class_id = _class_id(class_name)
            bind.exec_driver_sql(
                "INSERT OR IGNORE INTO classes (id, name) VALUES (?, ?)", (class_id, class_name)
            )
            bind.exec_driver_sql(
                "INSERT OR IGNORE INTO teacher_classes (teacher_login, class_id) VALUES (?, ?)",
                (login, class_id),
            )

    bind.exec_driver_sql("CREATE UNIQUE INDEX IF NOT EXISTS idx_students_student_id ON students (student_id)")
    bind.exec_driver_sql("CREATE INDEX IF NOT EXISTS idx_students_parent ON students (parent_id)")
    bind.exec_driver_sql("CREATE INDEX IF NOT EXISTS idx_students_class_id ON students (class_id)")
    bind.exec_driver_sql("CREATE INDEX IF NOT EXISTS idx_attendance_student_time ON attendance (student_id, timestamp, id)")
    bind.exec_driver_sql("CREATE INDEX IF NOT EXISTS idx_attendance_class_time ON attendance (class_name, timestamp, id)")
    bind.exec_driver_sql("CREATE INDEX IF NOT EXISTS idx_face_embeddings_student ON face_embeddings (student_id)")
    bind.exec_driver_sql("CREATE INDEX IF NOT EXISTS idx_auth_sessions_expiry ON auth_sessions (expires_at)")


def downgrade() -> None:
    bind = op.get_bind()
    tables = _tables(bind)
    # Compatibility columns retain the class information, so only normalized
    # relation tables are removed on downgrade.
    if "teacher_classes" in tables:
        op.drop_table("teacher_classes")
    if "classes" in tables:
        op.drop_table("classes")
