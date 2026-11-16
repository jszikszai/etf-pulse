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

from history_updater import plan_history_update  # noqa: E402


TARGET = date(2026, 11, 13)
HISTORY_TEXT = """[
  {
    "date": "2026-11-02",
    "VHYL": 82.96,
    "VWCE": 169.28,
    "EURHUF": 364.50,
    "cashHUF": 0
  },
  {
    "date": "2026-11-09",
    "VHYL": 82.97,
    "VWCE": 167.42,
    "EURHUF": 362.99,
    "cashHUF": 0
  }
]
"""


def staged_observation(symbol: str, value: str) -> dict[str, object]:
    common = {
        "observation": {"date": TARGET.isoformat(), "value": value},
        "verification": {"status": "verified"},
    }
    if symbol == "VHYL":
        return {
            **common,
            "instrument": {"symbol": symbol, "isin": "IE00B8GKDB10"},
            "market": {"mic": "XAMS", "currency": "EUR"},
            "observation": {
                "date": TARGET.isoformat(), "value": value,
                "unit": "EUR", "type": "unadjusted_close",
            },
        }
    if symbol == "VWCE":
        return {
            **common,
            "instrument": {"symbol": symbol, "isin": "IE00BK5BQT80"},
            "market": {"mic": "XETA", "currency": "EUR"},
            "observation": {
                "date": TARGET.isoformat(), "value": value,
                "unit": "EUR", "type": "closing_auction_price",
            },
        }
    return {
        **common,
        "instrument": {"symbol": symbol, "seriesKey": "EXR.D.HUF.EUR.SP00.A"},
        "market": {"mic": "ECB", "currency": "HUF"},
        "observation": {
            "date": TARGET.isoformat(), "value": value,
            "unit": "HUF_per_EUR", "type": "official_reference_rate",
        },
    }


def complete_staging() -> dict[str, object]:
    return {
        "observations": [
            staged_observation("VHYL", "84.445"),
            staged_observation("VWCE", "169.86"),
            staged_observation("EURHUF", "365.6"),
        ],
        "issues": [],
    }


class HistoryUpdaterTests(unittest.TestCase):
    def test_complete_verified_date_appends_without_changing_existing_records(self) -> None:
        before = json.loads(HISTORY_TEXT)
        baseline_text = HISTORY_TEXT[HISTORY_TEXT.index("  {") : HISTORY_TEXT.index("  },") + 3]
        plan = plan_history_update(HISTORY_TEXT, complete_staging(), TARGET)

        self.assertEqual(plan.status, "ready")
        self.assertIsNotNone(plan.updated_text)
        after = json.loads(plan.updated_text)
        self.assertEqual(after[:-1], before)
        self.assertEqual(after[-1], {
            "date": "2026-11-13", "VHYL": 84.445, "VWCE": 169.86,
            "EURHUF": 365.6, "cashHUF": 0,
        })
        self.assertIn(baseline_text, plan.updated_text)

    def test_incomplete_date_is_skipped(self) -> None:
        staging = complete_staging()
        staging["observations"] = staging["observations"][:-1]
        plan = plan_history_update(HISTORY_TEXT, staging, TARGET)
        self.assertEqual(plan.status, "skipped")
        self.assertIn("missing EURHUF", plan.message)
        self.assertIsNone(plan.updated_text)

    def test_unverified_date_is_skipped(self) -> None:
        staging = complete_staging()
        vwce = staging["observations"][1]
        vwce["verification"]["status"] = "unverified"
        plan = plan_history_update(HISTORY_TEXT, staging, TARGET)
        self.assertEqual(plan.status, "skipped")
        self.assertIn("VWCE is not verified", plan.message)

    def test_existing_date_is_skipped_without_duplicate(self) -> None:
        plan = plan_history_update(HISTORY_TEXT, complete_staging(), date(2026, 11, 9))
        self.assertEqual(plan.status, "skipped")
        self.assertEqual(plan.message, "date already exists in history")
        self.assertIsNone(plan.updated_text)

    def test_older_missing_date_is_not_inserted_or_reordered(self) -> None:
        staging = complete_staging()
        for item in staging["observations"]:
            item["observation"]["date"] = "2026-11-06"
        plan = plan_history_update(HISTORY_TEXT, staging, date(2026, 11, 6))
        self.assertEqual(plan.status, "skipped")
        self.assertIn("will not be reordered", plan.message)


if __name__ == "__main__":
    unittest.main()
