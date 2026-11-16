from __future__ import annotations

import io
import json
import sys
import tempfile
import unittest
from datetime import date, datetime, timezone
from email.message import Message
from pathlib import Path
from urllib.error import HTTPError


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = REPOSITORY_ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from market_data_collector import (  # noqa: E402
    DuplicateObservationError,
    HttpClient,
    IdentityError,
    MissingObservation,
    SourceFormatError,
    ValidationError,
    ecb_url,
    euronext_url,
    merge_staging,
    parse_collection_date,
    parse_ecb_csv,
    parse_euronext_csv,
    parse_xetra_records,
    write_staging,
    xetra_daily_url,
)


FIXTURES = Path(__file__).parent / "fixtures"
TARGET = date(2026, 11, 13)
RETRIEVED = "2026-11-16T20:00:00Z"


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


class EuronextTests(unittest.TestCase):
    def test_valid_unadjusted_close(self) -> None:
        result = parse_euronext_csv(
            fixture("euronext_valid.csv"),
            TARGET,
            source_url=euronext_url(TARGET),
            retrieved_at=RETRIEVED,
        )
        self.assertEqual(result["instrument"]["isin"], "IE00B8GKDB10")
        self.assertEqual(result["market"]["mic"], "XAMS")
        self.assertEqual(result["market"]["currency"], "EUR")
        self.assertEqual(result["observation"]["value"], "100.25")
        self.assertEqual(result["observation"]["type"], "unadjusted_close")

    def test_malformed_csv_fails(self) -> None:
        with self.assertRaises(SourceFormatError):
            parse_euronext_csv(
                fixture("euronext_malformed.csv"), TARGET,
                source_url=euronext_url(TARGET), retrieved_at=RETRIEVED,
            )

    def test_missing_date_is_not_invented(self) -> None:
        with self.assertRaises(MissingObservation):
            parse_euronext_csv(
                fixture("euronext_missing_date.csv"), TARGET,
                source_url=euronext_url(TARGET), retrieved_at=RETRIEVED,
            )

    def test_wrong_instrument_fails(self) -> None:
        with self.assertRaises(IdentityError):
            parse_euronext_csv(
                fixture("euronext_wrong_instrument.csv"), TARGET,
                source_url=euronext_url(TARGET), retrieved_at=RETRIEVED,
            )

    def test_wrong_exchange_url_fails(self) -> None:
        with self.assertRaises(IdentityError):
            parse_euronext_csv(
                fixture("euronext_valid.csv"), TARGET,
                source_url=euronext_url(TARGET).replace("-XAMS", "-XPAR"),
                retrieved_at=RETRIEVED,
            )

    def test_nonpositive_close_fails(self) -> None:
        with self.assertRaises(ValidationError):
            parse_euronext_csv(
                fixture("euronext_zero_price.csv"), TARGET,
                source_url=euronext_url(TARGET), retrieved_at=RETRIEVED,
            )


class EcbTests(unittest.TestCase):
    def test_valid_official_reference_rate(self) -> None:
        result = parse_ecb_csv(
            fixture("ecb_valid.csv"), TARGET,
            source_url=ecb_url(TARGET), retrieved_at=RETRIEVED,
        )
        self.assertEqual(result["instrument"]["seriesKey"], "EXR.D.HUF.EUR.SP00.A")
        self.assertEqual(result["observation"]["value"], "365.60")
        self.assertEqual(result["observation"]["unit"], "HUF_per_EUR")

    def test_missing_date_is_not_invented(self) -> None:
        with self.assertRaises(MissingObservation):
            parse_ecb_csv(
                fixture("ecb_missing_date.csv"), TARGET,
                source_url=ecb_url(TARGET), retrieved_at=RETRIEVED,
            )

    def test_wrong_series_fails(self) -> None:
        with self.assertRaises(IdentityError):
            parse_ecb_csv(
                fixture("ecb_wrong_instrument.csv"), TARGET,
                source_url=ecb_url(TARGET), retrieved_at=RETRIEVED,
            )


class XetraTests(unittest.TestCase):
    def test_selects_closing_auction_not_late_last_trade(self) -> None:
        result = parse_xetra_records(
            fixture("xetra_valid.ndjson").splitlines(), TARGET,
            source_url=xetra_daily_url(TARGET), retrieved_at=RETRIEVED,
        )
        self.assertEqual(result["market"]["mic"], "XETA")
        self.assertEqual(result["observation"]["value"], "100.25")
        self.assertNotEqual(result["observation"]["value"], "105.00")
        self.assertEqual(result["verification"]["status"], "verified")
        self.assertEqual(result["auctionCandidate"]["quantity"], "5175")

    def test_conflicting_regular_prices_are_unverified(self) -> None:
        result = parse_xetra_records(
            fixture("xetra_conflicting_clob.ndjson").splitlines(), TARGET,
            source_url=xetra_daily_url(TARGET), retrieved_at=RETRIEVED,
        )
        self.assertEqual(result["observation"]["value"], "100.25")
        self.assertEqual(result["observation"]["type"], "candidate_closing_auction_price")
        self.assertEqual(result["verification"]["status"], "unverified")

    def test_wrong_instrument_is_missing(self) -> None:
        with self.assertRaises(MissingObservation):
            parse_xetra_records(
                fixture("xetra_wrong_instrument.ndjson").splitlines(), TARGET,
                source_url=xetra_daily_url(TARGET), retrieved_at=RETRIEVED,
            )

    def test_wrong_currency_fails(self) -> None:
        with self.assertRaises(IdentityError):
            parse_xetra_records(
                fixture("xetra_wrong_currency.ndjson").splitlines(), TARGET,
                source_url=xetra_daily_url(TARGET), retrieved_at=RETRIEVED,
            )

    def test_wrong_venue_fails(self) -> None:
        with self.assertRaises(IdentityError):
            parse_xetra_records(
                fixture("xetra_wrong_venue.ndjson").splitlines(), TARGET,
                source_url=xetra_daily_url(TARGET), retrieved_at=RETRIEVED,
            )


class StagingTests(unittest.TestCase):
    @staticmethod
    def observation(day: str) -> dict[str, object]:
        return {
            "instrument": {"symbol": "VHYL", "isin": "IE00B8GKDB10"},
            "market": {"mic": "XAMS", "currency": "EUR"},
            "observation": {"date": day, "value": "100.25", "type": "unadjusted_close"},
        }

    def test_duplicate_observation_refuses_overwrite(self) -> None:
        existing = {"observations": [self.observation("2026-11-13")], "issues": []}
        collected = {
            "generatedAt": RETRIEVED,
            "observations": [self.observation("2026-11-13")],
            "issues": [],
        }
        with self.assertRaises(DuplicateObservationError):
            merge_staging(existing, collected)

    def test_new_date_is_appended(self) -> None:
        existing = {"observations": [self.observation("2026-11-13")], "issues": []}
        collected = {
            "generatedAt": RETRIEVED,
            "observations": [self.observation("2026-11-16")],
            "issues": [],
        }
        merged = merge_staging(existing, collected)
        self.assertEqual(len(merged["observations"]), 2)

    def test_series_key_is_a_valid_observation_identity(self) -> None:
        fx = {
            "instrument": {"symbol": "EURHUF", "seriesKey": "EXR.D.HUF.EUR.SP00.A"},
            "market": {"mic": "ECB", "currency": "HUF"},
            "observation": {
                "date": "2026-11-13",
                "value": "365.60",
                "type": "official_reference_rate",
            },
        }
        merged = merge_staging(
            None,
            {"generatedAt": RETRIEVED, "observations": [fx], "issues": []},
        )
        self.assertEqual(len(merged["observations"]), 1)

    def test_protected_output_is_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            write_staging(
                REPOSITORY_ROOT / "data" / "history.json",
                {"observations": [], "issues": []},
                REPOSITORY_ROOT,
            )

    def test_safe_staging_write_does_not_change_protected_files(self) -> None:
        protected = [
            REPOSITORY_ROOT / "index.html",
            REPOSITORY_ROOT / "data" / "history.json",
            REPOSITORY_ROOT / "data" / "baseline.json",
        ]
        before = [path.read_bytes() for path in protected]
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "staging.json"
            write_staging(output, {"schemaVersion": 1, "observations": [], "issues": []}, REPOSITORY_ROOT)
            self.assertTrue(output.exists())
        self.assertEqual(before, [path.read_bytes() for path in protected])


class SafetyAndRetryTests(unittest.TestCase):
    def test_baseline_date_is_rejected(self) -> None:
        with self.assertRaises(ValidationError):
            parse_collection_date("2026-11-02")

    def test_http_429_is_retried(self) -> None:
        calls = []
        sleeps = []

        class Response(io.BytesIO):
            def close(self) -> None:
                super().close()

        def opener(request, timeout):
            calls.append(request.full_url)
            if len(calls) == 1:
                headers = Message()
                headers["Retry-After"] = "0"
                raise HTTPError(request.full_url, 429, "rate limited", headers, None)
            return Response(b"ok")

        client = HttpClient(attempts=2, opener=opener, sleeper=sleeps.append)
        result = client.get_bytes("https://example.invalid/test")
        self.assertEqual(result.body, b"ok")
        self.assertEqual(len(calls), 2)
        self.assertEqual(sleeps, [0.0])


if __name__ == "__main__":
    unittest.main()
