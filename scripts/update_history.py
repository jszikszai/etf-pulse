#!/usr/bin/env python3
"""Append one complete verified staged date to data/history.json."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SCRIPT_DIRECTORY = Path(__file__).resolve().parent
if str(SCRIPT_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIRECTORY))

from history_updater import plan_history_update, write_history_atomic
from market_data_collector import CollectorError, parse_collection_date


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", required=True, help="Staged date in YYYY-MM-DD format")
    parser.add_argument(
        "--staging",
        type=Path,
        default=Path(".staging/market-data.json"),
        help="Staging JSON path (default: .staging/market-data.json)",
    )
    parser.add_argument(
        "--history",
        type=Path,
        default=Path("data/history.json"),
        help="History JSON path (default: data/history.json)",
    )
    parser.add_argument("--dry-run", action="store_true", help="Validate without writing history")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    repository_root = SCRIPT_DIRECTORY.parent
    staging_path = args.staging if args.staging.is_absolute() else repository_root / args.staging
    history_path = args.history if args.history.is_absolute() else repository_root / args.history
    expected_history = (repository_root / "data" / "history.json").resolve()
    if history_path.resolve() != expected_history:
        print("History update failed safely: only data/history.json may be updated.", file=sys.stderr)
        return 1
    try:
        target_date = parse_collection_date(args.date)
        staging = json.loads(staging_path.read_text(encoding="utf-8"))
        if not isinstance(staging, dict):
            raise ValueError("staging root must be an object")
        history_text = history_path.read_bytes().decode("utf-8")
        plan = plan_history_update(history_text, staging, target_date)
        if plan.status != "ready":
            print(f"Skipped {target_date.isoformat()}: {plan.message}")
            return 0
        if args.dry_run:
            print(f"Dry run ready for {target_date.isoformat()}: {plan.record}")
            return 0
        write_history_atomic(history_path, plan)
        print(f"Appended verified history record for {target_date.isoformat()}.")
        return 0
    except (CollectorError, OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        print(f"History update failed safely: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
