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

from promote_staged_history import promote_staged_dates  # noqa: E402


HISTORY = """[
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


def observation(symbol: str, day: str, value: str, verified: bool = True) -> dict[str, object]:
    verification = {"status": "verified" if verified else "unverified"}
    if symbol == "VHYL":
        return {
            "instrument": {"symbol": symbol, "isin": "IE00B8GKDB10"},
            "market": {"mic": "XAMS", "currency": "EUR"},
            "observation": {
                "date": day, "value": value, "unit": "EUR", "type": "unadjusted_close",
            },
            "verification": verification,
        }
    if symbol == "VWCE":
        return {
            "instrument": {"symbol": symbol, "isin": "IE00BK5BQT80"},
            "market": {"mic": "XETA", "currency": "EUR"},
            "observation": {
                "date": day, "value": value, "unit": "EUR",
                "type": "closing_auction_price" if verified else "candidate_closing_auction_price",
            },
            "verification": verification,
        }
    return {
        "instrument": {"symbol": symbol, "seriesKey": "EXR.D.HUF.EUR.SP00.A"},
        "market": {"mic": "ECB", "currency": "HUF"},
        "observation": {
            "date": day, "value": value, "unit": "HUF_per_EUR",
            "type": "official_reference_rate",
        },
        "verification": verification,
    }


def complete_date(day: str, seed: int) -> list[dict[str, object]]:
    return [
        observation("VHYL", day, f"8{seed}.1"),
        observation("VWCE", day, f"16{seed}.2"),
        observation("EURHUF", day, f"36{seed}.3"),
    ]


class AutomaticPromotionTests(unittest.TestCase):
    def test_multiple_complete_dates_are_appended_in_order(self) -> None:
        staging = {
            "observations": [
                *complete_date("2026-11-10", 1),
                *complete_date("2026-11-11", 2),
            ],
            "issues": [],
            "batch": {
                "attemptedDates": ["2026-11-10", "2026-11-11"],
                "noMarketEvidenceDates": [],
            },
        }
        result = promote_staged_dates(HISTORY, staging)
        updated = json.loads(result.updated_text)
        self.assertEqual(result.promoted_dates, (date(2026, 11, 10), date(2026, 11, 11)))
        self.assertEqual([item["date"] for item in updated], [
            "2026-11-02", "2026-11-09", "2026-11-10", "2026-11-11",
        ])
        self.assertEqual(updated[:2], json.loads(HISTORY))
        self.assertEqual([item["cashHUF"] for item in updated[-2:]], [0, 0])

    def test_missing_november_10_does_not_block_complete_november_13(self) -> None:
        staging = {
            "observations": [
                observation("VHYL", "2026-11-10", "81.1"),
                observation("EURHUF", "2026-11-10", "361.1"),
                *complete_date("2026-11-13", 3),
            ],
            "issues": [],
            "batch": {
                "attemptedDates": ["2026-11-10", "2026-11-13"],
                "noMarketEvidenceDates": [],
            },
        }
        result = promote_staged_dates(HISTORY, staging)
        updated = json.loads(result.updated_text)
        self.assertEqual(result.missing_dates, (date(2026, 11, 10),))
        self.assertEqual(result.promoted_dates, (date(2026, 11, 13),))
        self.assertEqual(updated[-1]["date"], "2026-11-13")
        self.assertEqual(updated[:-1], json.loads(HISTORY))

    def test_no_complete_dates_leaves_history_unchanged(self) -> None:
        staging = {
            "observations": [],
            "issues": [],
            "batch": {
                "attemptedDates": ["2026-11-10"],
                "noMarketEvidenceDates": ["2026-11-10"],
            },
        }
        result = promote_staged_dates(HISTORY, staging)
        self.assertEqual(result.updated_text, HISTORY)
        self.assertEqual(result.promoted_dates, ())
        self.assertEqual(result.missing_dates, ())


if __name__ == "__main__":
    unittest.main()
