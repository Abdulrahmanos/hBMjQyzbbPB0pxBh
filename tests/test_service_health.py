"""Container health must depend on fresh accepted quotes, not just a database."""
import sqlite3
import unittest
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from tests.test_foundation import temporary_directory
from tools.check_service_health import main


class ServiceHealthTests(unittest.TestCase):
    def run_check(self, path):
        with patch("sys.argv", ["check_service_health.py", "--db", str(path)]):
            return main()

    def test_absent_database_is_unhealthy_without_creating_one(self):
        with temporary_directory() as directory:
            path = Path(directory) / "absent.sqlite3"
            self.assertEqual(self.run_check(path), 1)
            self.assertFalse(path.exists())

    def test_only_fresh_accepted_quotes_are_healthy(self):
        with temporary_directory() as directory:
            path = Path(directory) / "health.sqlite3"
            with closing(sqlite3.connect(path)) as database:
                database.execute("CREATE TABLE paper_quotes (observed_at TEXT, accepted INTEGER)")
                now = datetime.now(timezone.utc)
                for timestamp, accepted, expected in [
                    ((now - timedelta(minutes=5)).isoformat(), 1, 1),
                    (now.isoformat(), 0, 1),
                    ((now + timedelta(minutes=5)).isoformat(), 1, 1),
                    (now.replace(tzinfo=None).isoformat(), 1, 1),
                    (now.isoformat(), 1, 0),
                ]:
                    with self.subTest(timestamp=timestamp, accepted=accepted):
                        database.execute("DELETE FROM paper_quotes")
                        database.execute("INSERT INTO paper_quotes VALUES (?, ?)", (timestamp, accepted))
                        database.commit()
                        self.assertEqual(self.run_check(path), expected)


if __name__ == "__main__":
    unittest.main()
