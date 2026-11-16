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
    collectible_dates,
    parse_available_archive_dates,
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

    def test_collectible_dates_are_missing_new_and_not_after_cutoff(self) -> None:
        result = collectible_dates(
            [date(2026, 11, 2), date(2026, 11, 9)],
            [date(2026, 11, 9), date(2026, 11, 13), date(2026, 11, 16)],
            date(2026, 11, 15),
        )
        self.assertEqual(result, [date(2026, 11, 13)])

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
            "batch": {"attemptedDates": ["2026-11-16"]},
        }
        text = summarize(document)
        self.assertIn("Verified observations: 1", text)
        self.assertIn("Unverified observations: 1", text)
        self.assertIn("Missing/error issues: 1", text)
        self.assertIn("archive_unavailable", text)

    def test_malformed_archive_index_fails(self) -> None:
        with self.assertRaises(SourceFormatError):
            parse_available_archive_dates(b"{}")


if __name__ == "__main__":
    unittest.main()
