#!/usr/bin/env python3
"""Promote staged dates in order, stopping before the first unresolved gap."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Mapping

SCRIPT_DIRECTORY = Path(__file__).resolve().parent
if str(SCRIPT_DIRECTORY) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIRECTORY))

from history_updater import HistoryUpdatePlan, plan_history_update, write_history_atomic
from market_data_collector import CollectorError, SourceFormatError, ValidationError


@dataclass(frozen=True)
class PromotionResult:
    updated_text: str
    promoted_dates: tuple[date, ...]
    skipped_non_trading_dates: tuple[date, ...]
    missing_dates: tuple[date, ...]
    missing_reasons: tuple[str, ...]


def _date_list(raw: object, context: str) -> list[date]:
    if not isinstance(raw, list):
        raise SourceFormatError(f"{context} must be an array.")
    values: list[date] = []
    for item in raw:
        if not isinstance(item, str):
            raise SourceFormatError(f"{context} must contain ISO date strings.")
        try:
            values.append(date.fromisoformat(item))
        except ValueError as exc:
            raise SourceFormatError(f"Invalid date in {context}: {item!r}") from exc
    if len(values) != len(set(values)) or values != sorted(values):
        raise ValidationError(f"{context} must contain unique chronological dates.")
    return values


def promote_staged_dates(history_text: str, staging: Mapping[str, object]) -> PromotionResult:
    batch = staging.get("batch")
    if not isinstance(batch, Mapping):
        raise SourceFormatError("Staging document has no batch metadata.")
    attempted = _date_list(batch.get("attemptedDates"), "batch.attemptedDates")
    no_market_evidence = set(
        _date_list(batch.get("noMarketEvidenceDates", []), "batch.noMarketEvidenceDates")
    )
    if not no_market_evidence.issubset(set(attempted)):
        raise ValidationError("Non-trading dates must be a subset of attempted dates.")

    current_text = history_text
    promoted: list[date] = []
    skipped: list[date] = []
    missing: list[date] = []
    missing_reasons: list[str] = []
    for target_date in attempted:
        if target_date in no_market_evidence:
            skipped.append(target_date)
            continue
        plan = plan_history_update(current_text, staging, target_date)
        if plan.status == "ready" and plan.updated_text is not None:
            current_text = plan.updated_text
            promoted.append(target_date)
            continue
        if plan.message == "date already exists in history":
            continue
        missing.append(target_date)
        missing_reasons.append(plan.message)

    return PromotionResult(
        current_text,
        tuple(promoted),
        tuple(skipped),
        tuple(missing),
        tuple(missing_reasons),
    )


def promotion_summary(result: PromotionResult) -> str:
    promoted = ", ".join(value.isoformat() for value in result.promoted_dates) or "none"
    skipped = ", ".join(value.isoformat() for value in result.skipped_non_trading_dates) or "none"
    missing = ", ".join(value.isoformat() for value in result.missing_dates) or "none"
    lines = [
        "## ETF Pulse history promotion",
        "",
        f"- Promoted complete dates: {promoted}",
        f"- Skipped dates with no market evidence: {skipped}",
        f"- Missing historical dates: {missing}",
    ]
    for missing_date, reason in zip(result.missing_dates, result.missing_reasons):
        lines.append(f"- `{missing_date.isoformat()}` remains absent: {reason}")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--staging", type=Path, default=Path(".staging/daily-market-data.json")
    )
    parser.add_argument("--history", type=Path, default=Path("data/history.json"))
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    repository_root = SCRIPT_DIRECTORY.parent
    staging_path = args.staging if args.staging.is_absolute() else repository_root / args.staging
    history_path = args.history if args.history.is_absolute() else repository_root / args.history
    expected_history = (repository_root / "data" / "history.json").resolve()
    if history_path.resolve() != expected_history:
        print("Promotion failed safely: only data/history.json may be updated.", file=sys.stderr)
        return 1
    try:
        staging = json.loads(staging_path.read_text(encoding="utf-8"))
        if not isinstance(staging, dict):
            raise SourceFormatError("Staging JSON root must be an object.")
        original_text = history_path.read_bytes().decode("utf-8")
        result = promote_staged_dates(original_text, staging)
        if result.updated_text != original_text:
            final_plan = HistoryUpdatePlan(
                "ready",
                "ordered staged dates ready to write",
                result.promoted_dates[-1],
                updated_text=result.updated_text,
            )
            write_history_atomic(history_path, final_plan)
        print(promotion_summary(result))
        return 0
    except (CollectorError, OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        print(f"Promotion failed safely: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
