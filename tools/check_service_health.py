"""Read-only health check for an ordinary continuous paper service."""
import argparse
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default="var/service/ledger.sqlite3")
    args = parser.parse_args()
    path = Path(args.db).resolve()
    if not path.is_file():
        return 1
    try:
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=2)) as database:
            last = database.execute("SELECT max(observed_at) FROM paper_quotes WHERE accepted=1").fetchone()[0]
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(last)).total_seconds() if last else float("inf")
        return 0 if -5 <= age <= 120 else 1
    except (sqlite3.Error, OSError, ValueError, TypeError):
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
