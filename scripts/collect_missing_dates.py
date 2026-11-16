#!/usr/bin/env python3
"""Collect missing history dates for which an official Xetra archive exists."""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path
from typing import Iterable, Mapping

SCRIPT_DIRECTORY = Path(__file__).resolve().parent
if str(SCRIPT_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIRECTORY))

from market_data_collector import (
    CollectorError,
    HttpClient,
    SourceFormatError,
    ValidationError,
    collect_date,
    merge_staging,
    parse_collection_date,
    utc_now_text,
    write_staging,
)


ARCHIVE_INDEX_URL = "https://mfs.deutsche-boerse.com/api/DETR-posttrade"
DAILY_ARCHIVE_PATTERN = re.compile(r"^DETR-posttrade-daily-(\d{4}-\d{2}-\d{2})\.json\.gz$")


def read_history_dates(path: Path) -> list[date]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SourceFormatError(f"Cannot read portfolio history {path}: {exc}") from exc
    if not isinstance(document, list) or not document:
        raise SourceFormatError("Portfolio history must be a non-empty array.")
    result: list[date] = []
    for record in document:
        if not isinstance(record, Mapping) or not isinstance(record.get("date"), str):
            raise SourceFormatError("Every history record must contain a string date.")
        try:
            result.append(date.fromisoformat(record["date"]))
        except ValueError as exc:
            raise SourceFormatError(f"Invalid history date: {record['date']!r}") from exc
    if len(result) != len(set(result)) or result != sorted(result):
        raise ValidationError("Portfolio history dates must be unique and chronological.")
    return result


def parse_available_archive_dates(payload: bytes) -> list[date]:
    try:
        document = json.loads(payload.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SourceFormatError(f"Deutsche Boerse archive index is invalid JSON: {exc}") from exc
    if not isinstance(document, Mapping) or not isinstance(document.get("CurrentFiles"), list):
        raise SourceFormatError("Deutsche Boerse archive index has no CurrentFiles array.")
    dates: set[date] = set()
    for filename in document["CurrentFiles"]:
        if not isinstance(filename, str):
            continue
        match = DAILY_ARCHIVE_PATTERN.fullmatch(filename)
        if match:
            try:
                dates.add(date.fromisoformat(match.group(1)))
            except ValueError:
                continue
    return sorted(dates)


def collectible_dates(
    history_dates: Iterable[date], available_dates: Iterable[date], through_date: date
) -> list[date]:
    history = set(history_dates)
    if not history:
        raise ValidationError("Portfolio history cannot be empty.")
    latest = max(history)
    return sorted(
        candidate
        for candidate in set(available_dates)
        if latest < candidate <= through_date and candidate not in history
    )


def batch_issue(through_date: date, status: str, reason: str) -> dict[str, str]:
    return {
        "instrument": "VWCE",
        "date": through_date.isoformat(),
        "source": "Deutsche Boerse archive index",
        "sourceUrl": ARCHIVE_INDEX_URL,
        "status": status,
        "reason": reason,
    }


def summarize(document: Mapping[str, object]) -> str:
    observations = document.get("observations", [])
    issues = document.get("issues", [])
    if not isinstance(observations, list) or not isinstance(issues, list):
        raise SourceFormatError("Batch staging document has invalid result arrays.")
    verified = 0
    unverified = 0
    for item in observations:
        if not isinstance(item, Mapping):
            continue
        verification = item.get("verification")
        if isinstance(verification, Mapping) and verification.get("status") == "verified":
            verified += 1
        else:
            unverified += 1
    batch = document.get("batch")
    attempted = batch.get("attemptedDates", []) if isinstance(batch, Mapping) else []
    available = batch.get("availableXetraArchiveDates", []) if isinstance(batch, Mapping) else []
    lines = [
        "## ETF Pulse collection summary",
        "",
        f"- Available official Xetra daily archives: {', '.join(available) if available else 'none'}",
        f"- Attempted dates: {', '.join(attempted) if attempted else 'none'}",
        f"- Verified observations: {verified}",
        f"- Unverified observations: {unverified}",
        f"- Missing/error issues: {len(issues)}",
    ]
    for item in issues:
        if isinstance(item, Mapping):
            lines.append(
                f"- `{item.get('date', 'unknown')}` {item.get('instrument', 'source')}: "
                f"{item.get('status', 'issue')} — {item.get('reason', 'no detail')}"
            )
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--through", required=True, help="Last eligible date in YYYY-MM-DD format")
    parser.add_argument(
        "--history", type=Path, default=Path("data/history.json"),
        help="History path used only to find missing dates",
    )
    parser.add_argument(
        "--output", type=Path, default=Path(".staging/daily-market-data.json"),
        help="Fresh staging artifact path",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    repository_root = SCRIPT_DIRECTORY.parent
    history_path = args.history if args.history.is_absolute() else repository_root / args.history
    output_path = args.output if args.output.is_absolute() else repository_root / args.output
    try:
        through_date = parse_collection_date(args.through)
        history_dates = read_history_dates(history_path)
        generated_at = utc_now_text()
        client = HttpClient()
        available_dates: list[date] = []
        attempted_dates: list[date] = []
        document: dict[str, object] = {
            "schemaVersion": 1,
            "generatedAt": generated_at,
            "observations": [],
            "issues": [],
        }
        try:
            index = client.get_bytes(ARCHIVE_INDEX_URL)
            available_dates = parse_available_archive_dates(index.body)
            attempted_dates = collectible_dates(history_dates, available_dates, through_date)
            for target_date in attempted_dates:
                collected = collect_date(target_date, http=client)
                document = merge_staging(document, collected)
        except CollectorError as exc:
            document["issues"] = [
                *document.get("issues", []),
                batch_issue(through_date, "archive_index_unavailable", str(exc)),
            ]

        document["batch"] = {
            "historyLatestDate": max(history_dates).isoformat(),
            "throughDate": through_date.isoformat(),
            "availableXetraArchiveDates": [value.isoformat() for value in available_dates],
            "attemptedDates": [value.isoformat() for value in attempted_dates],
            "note": (
                "Only dates advertised by the official Deutsche Boerse daily archive index "
                "are attempted; history is never modified."
            ),
        }
        write_staging(output_path, document, repository_root)
        print(summarize(document))
        return 0
    except (CollectorError, OSError, ValueError) as exc:
        print(f"Batch collection failed safely: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
