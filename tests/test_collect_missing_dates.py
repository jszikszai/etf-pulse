from __future__ import annotations

import json
import sys
import unittest
from datetime import date
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPOSITORY_ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from collect_missing_dates import (  # noqa: E402
    collection_state,
    parse_available_archive_dates,
    pending_weekdays,
    summarize,
)
from market_data_collector import SourceFormatError  # noqa: E402


class MissingDateBatchTests(unittest.TestCase):
    def test_archive_index_returns_only_daily_archive_dates(self) -> None:
        payload = json.dumps(
            {
                "CurrentFiles": [
                    "DETR-posttrade-2026-11-16T16_36.json.gz",
                    "DETR-posttrade-daily-2026-11-13.json.gz",
                    "DETR-posttrade-daily-2026-11-16.json.gz",
                ]
            }
        ).encode()
        self.assertEqual(
            parse_available_archive_dates(payload),
            [date(2026, 11, 13), date(2026, 11, 16)],
        )

    def test_pending_weekdays_are_chronological_and_after_history(self) -> None:
        result = pending_weekdays(
            [date(2026, 11, 2), date(2026, 11, 9)],
            date(2026, 11, 15),
        )
        self.assertEqual(
            result,
            [date(2026, 11, 10), date(2026, 11, 11), date(2026, 11, 12), date(2026, 11, 13)],
        )

    def test_collection_state_marks_partial_or_unverified_date_as_gap(self) -> None:
        partial = {
            "observations": [
                {
                    "instrument": {"symbol": "VHYL"},
                    "verification": {"status": "verified"},
                },
                {
                    "instrument": {"symbol": "VWCE"},
                    "verification": {"status": "unverified"},
                },
            ]
        }
        self.assertEqual(collection_state(partial), "gap")
        self.assertEqual(collection_state({"observations": []}), "no_market_evidence")

    def test_summary_reports_unverified_and_missing_values(self) -> None:
        document = {
            "observations": [
                {"verification": {"status": "verified"}},
                {"verification": {"status": "unverified"}},
            ],
            "issues": [
                {
                    "date": "2026-11-16",
                    "instrument": "VWCE",
                    "status": "archive_unavailable",
                    "reason": "HTTP 404",
                }
            ],
            "batch": {
                "attemptedDates": ["2026-11-16"],
                "missingHistoricalDates": ["2026-11-16"],
            },
        }
        text = summarize(document)
        self.assertIn("Verified observations: 1", text)
        self.assertIn("Unverified observations: 1", text)
        self.assertIn("Missing/error issues: 1", text)
        self.assertIn("Missing historical dates: 2026-11-16", text)
        self.assertIn("archive_unavailable", text)

    def test_malformed_archive_index_fails(self) -> None:
        with self.assertRaises(SourceFormatError):
            parse_available_archive_dates(b"{}")


if __name__ == "__main__":
    unittest.main()
