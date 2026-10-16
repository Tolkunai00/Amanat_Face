"""Explicit, idempotent notification recovery for a selected event date range."""

import argparse

from school_face_id.db.migrations import init_db
from school_face_id.services.notifications import backfill_date_range


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--from-date", required=True, help="First event date, YYYY-MM-DD")
    parser.add_argument("--to-date", help="Last event date, YYYY-MM-DD")
    parser.add_argument("--limit", type=int, default=100000)
    return parser.parse_args()


def main():
    args = parse_args()
    init_db()
    result = backfill_date_range(args.from_date, args.to_date, max(1, args.limit))
    print(
        f"Events checked: {result['events_checked']}; "
        f"notifications created: {result['notifications_created']}"
    )


if __name__ == "__main__":
    main()
