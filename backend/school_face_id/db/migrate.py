"""Explicit database migration command for deployment workflows."""

from __future__ import annotations

import argparse

from .migrations import init_db, migration_status


def main(argv=None):
    parser = argparse.ArgumentParser(description="Amanat Face database migration command")
    parser.add_argument("command", choices=("upgrade", "status"))
    args = parser.parse_args(argv)
    if args.command == "upgrade":
        init_db()
        print("Database upgraded to Alembic head")
        return 0
    result = migration_status()
    print(result)
    return 0 if result["status"] == "ready" else 1


if __name__ == "__main__":
    raise SystemExit(main())
