"""Alembic migration entry point."""

from pathlib import Path

from ..config.settings import BACKEND_ROOT, DATABASE_URL, DB_NAME
from .connection import get_database_url


def init_db():
    """Upgrade the configured database to the latest Alembic revision."""
    from alembic import command
    from alembic.config import Config

    if not DATABASE_URL:
        Path(DB_NAME).resolve().parent.mkdir(parents=True, exist_ok=True)
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "migrations"))
    config.set_main_option("sqlalchemy.url", get_database_url())
    command.upgrade(config, "head")


def migration_status():
    """Compare the database revision with Alembic head without mutating schema."""
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "migrations"))
    heads = tuple(ScriptDirectory.from_config(config).get_heads())

    from .connection import get_connection
    connection = get_connection()
    try:
        rows = connection.execute("SELECT version_num FROM alembic_version").fetchall()
    finally:
        connection.close()
    current = tuple(str(row[0]) for row in rows)
    return {
        "status": "ready" if set(current) == set(heads) and bool(heads) else "not_ready",
        "current": list(current),
        "head": list(heads),
    }
