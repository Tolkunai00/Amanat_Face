"""Manually run the attendance event screenshot retention cleanup."""

import argparse
from pathlib import Path
import sys


BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from school_face_id.db.migrations import init_db
from school_face_id.services.event_photos import cleanup_expired_event_photos


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run", action="store_true", help="report expired photos without deleting files"
    )
    args = parser.parse_args()
    init_db()
    stats = cleanup_expired_event_photos(dry_run=args.dry_run)
    for key in ("scanned", "deleted", "missing", "failed", "would_delete"):
        if key in stats:
            print(f"{key}: {stats[key]}")


if __name__ == "__main__":
    main()
