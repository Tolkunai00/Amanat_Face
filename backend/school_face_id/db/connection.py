"""PostgreSQL runtime connections with isolated SQLite migration-test support."""

import re
import sqlite3
from pathlib import Path

from ..config.settings import DATABASE_URL, DB_NAME, SQLITE_TEST_MODE


def get_database_url():
    """Require PostgreSQL at runtime; SQLite is opt-in for isolated tests only."""
    if DATABASE_URL:
        return DATABASE_URL
    if SQLITE_TEST_MODE:
        database_path = Path(DB_NAME).resolve()
        return f"sqlite:///{database_path.as_posix()}"
    raise RuntimeError(
        "DATABASE_URL is required. Amanat uses PostgreSQL as its only runtime database."
    )


def _postgres_sql(sql):
    """Translate the small legacy SQLite SQL surface used by repositories."""
    statement = sql.strip()
    if statement.upper().startswith("DELETE FROM SQLITE_SEQUENCE"):
        return None
    if statement.upper() == "BEGIN IMMEDIATE":
        return "BEGIN"

    sql = sql.replace("?", "%s")
    sql = re.sub(
        r"([A-Za-z_][A-Za-z0-9_.]*)\s*=\s*%s\s+COLLATE\s+NOCASE",
        r"LOWER(\1) = LOWER(%s)",
        sql,
        flags=re.IGNORECASE,
    )
    if re.search(r"INSERT\s+OR\s+IGNORE\s+INTO", sql, flags=re.IGNORECASE):
        sql = re.sub(
            r"INSERT\s+OR\s+IGNORE\s+INTO", "INSERT INTO", sql, count=1, flags=re.IGNORECASE
        ).rstrip().rstrip(";") + " ON CONFLICT DO NOTHING"
    sql = re.sub(r"date\('now'\s*,\s*'localtime'\)", "CURRENT_DATE", sql, flags=re.IGNORECASE)
    sql = re.sub(
        r"datetime\('now'\)",
        "to_char(CURRENT_TIMESTAMP, 'YYYY-MM-DD HH24:MI:SS')",
        sql,
        flags=re.IGNORECASE,
    )
    sql = re.sub(
        r"date\(([A-Za-z_][A-Za-z0-9_.]*)\)",
        r"CAST(\1 AS timestamp)::date",
        sql,
        flags=re.IGNORECASE,
    )
    sql = re.sub(
        r"time\(([A-Za-z_][A-Za-z0-9_.]*)\)",
        r"CAST(\1 AS timestamp)::time",
        sql,
        flags=re.IGNORECASE,
    )
    return sql


class PostgresCursor:
    def __init__(self, cursor):
        self._cursor = cursor
        self._skipped = False

    @property
    def rowcount(self):
        return 0 if self._skipped else self._cursor.rowcount

    def execute(self, sql, params=()):
        translated = _postgres_sql(sql)
        self._skipped = translated is None
        if translated is not None:
            self._cursor.execute(translated, tuple(params))
        return self

    def fetchone(self):
        return None if self._skipped else self._cursor.fetchone()

    def fetchall(self):
        return [] if self._skipped else self._cursor.fetchall()


class PostgresConnection:
    def __init__(self, connection):
        self._connection = connection

    def cursor(self):
        return PostgresCursor(self._connection.cursor())

    def execute(self, sql, params=()):
        return self.cursor().execute(sql, params)

    def commit(self):
        self._connection.commit()

    def rollback(self):
        self._connection.rollback()

    def close(self):
        self._connection.close()


def is_integrity_error(exc):
    if isinstance(exc, sqlite3.IntegrityError):
        return True
    # PostgreSQL integrity errors use SQLSTATE class 23 (unique/FK/not-null/check).
    return exc.__class__.__module__.startswith("psycopg") and str(
        getattr(exc, "sqlstate", "")
    ).startswith("23")


def get_connection():
    if DATABASE_URL:
        import psycopg

        connection_url = DATABASE_URL.replace("postgresql+psycopg://", "postgresql://", 1)
        return PostgresConnection(psycopg.connect(connection_url))
    if SQLITE_TEST_MODE:
        Path(DB_NAME).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(DB_NAME, timeout=15)
        conn.execute("PRAGMA busy_timeout = 15000")
        conn.execute("PRAGMA foreign_keys = ON")
        return conn
    raise RuntimeError(
        "DATABASE_URL is required. Start PostgreSQL with Docker Compose or provide a PostgreSQL URL."
    )
