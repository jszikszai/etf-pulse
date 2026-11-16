#!/usr/bin/env python3
"""Collect one date of official market observations into local staging."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SCRIPT_DIRECTORY = Path(__file__).resolve().parent
if str(SCRIPT_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIRECTORY))

from market_data_collector import (
    CollectorError,
    collect_date,
    merge_staging,
    parse_collection_date,
    read_staging,
    write_staging,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", required=True, help="Trading date in YYYY-MM-DD format")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path(".staging/market-data.json"),
        help="Staging JSON path (default: .staging/market-data.json)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Fetch, validate and print results without writing staging JSON",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    repository_root = Path(__file__).resolve().parent.parent
    try:
        target_date = parse_collection_date(args.date)
        collected = collect_date(target_date)
        if args.dry_run:
            print(json.dumps(collected, ensure_ascii=False, indent=2))
            return 0

        output = args.output
        if not output.is_absolute():
            output = repository_root / output
        existing = read_staging(output)
        merged = merge_staging(existing, collected)
        write_staging(output, merged, repository_root)
        print(
            f"Staged {len(collected['observations'])} observations and "
            f"{len(collected['issues'])} issues in {output}."
        )
        return 0
    except CollectorError as exc:
        print(f"Collection failed safely: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
