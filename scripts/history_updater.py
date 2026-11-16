"""Promote complete, verified staged observations into portfolio history."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Mapping

from market_data_collector import (
    BASELINE_DATE,
    ECB_SERIES_KEY,
    VHYL_CURRENCY,
    VHYL_ISIN,
    VHYL_MIC,
    VWCE_CURRENCY,
    VWCE_ISIN,
    VWCE_VENUES,
    SourceFormatError,
    ValidationError,
    positive_decimal,
)


@dataclass(frozen=True)
class HistoryUpdatePlan:
    status: str
    message: str
    target_date: date
    record: dict[str, object] | None = None
    updated_text: str | None = None


def _required_mapping(value: object, context: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise SourceFormatError(f"{context} must be an object.")
    return value


def _history_records(history_text: str) -> list[dict[str, object]]:
    try:
        document = json.loads(history_text)
    except json.JSONDecodeError as exc:
        raise SourceFormatError(f"Portfolio history is invalid JSON: {exc}") from exc
    if not isinstance(document, list) or not document:
        raise SourceFormatError("Portfolio history must be a non-empty array.")
    if not all(isinstance(item, dict) for item in document):
        raise SourceFormatError("Every portfolio history record must be an object.")

    parsed_dates: list[date] = []
    for item in document:
        raw_date = item.get("date")
        if not isinstance(raw_date, str):
            raise SourceFormatError("Every history record must have a string date.")
        try:
            parsed_dates.append(date.fromisoformat(raw_date))
        except ValueError as exc:
            raise SourceFormatError(f"Invalid history date: {raw_date!r}") from exc
    if len(parsed_dates) != len(set(parsed_dates)):
        raise ValidationError("Portfolio history already contains duplicate dates.")
    if parsed_dates != sorted(parsed_dates):
        raise ValidationError("Portfolio history is not in chronological order.")
    if parsed_dates.count(BASELINE_DATE) != 1:
        raise ValidationError("Portfolio history must contain exactly one immutable baseline date.")
    return document


def _matching_observations(
    staging: Mapping[str, object], target_date: date
) -> dict[str, Mapping[str, object]]:
    observations = staging.get("observations")
    if not isinstance(observations, list):
        raise SourceFormatError("Staging document has no observations array.")

    matches: dict[str, Mapping[str, object]] = {}
    for raw in observations:
        item = _required_mapping(raw, "staged observation")
        observed = _required_mapping(item.get("observation"), "staged observation.observation")
        if observed.get("date") != target_date.isoformat():
            continue
        instrument = _required_mapping(item.get("instrument"), "staged observation.instrument")
        symbol = instrument.get("symbol")
        if symbol not in {"VHYL", "VWCE", "EURHUF"}:
            continue
        if symbol in matches:
            raise ValidationError(
                f"Staging contains duplicate {symbol} observations for {target_date.isoformat()}."
            )
        matches[str(symbol)] = item
    return matches


def _verified_value(
    item: Mapping[str, object],
    *,
    symbol: str,
    expected_type: str,
) -> str:
    verification = _required_mapping(item.get("verification"), f"{symbol}.verification")
    if verification.get("status") != "verified":
        raise ValidationError(f"{symbol} is not verified.")
    observed = _required_mapping(item.get("observation"), f"{symbol}.observation")
    if observed.get("type") != expected_type:
        raise ValidationError(
            f"{symbol} price type mismatch: expected {expected_type!r}, found {observed.get('type')!r}."
        )
    _, text = positive_decimal(observed.get("value"), f"{symbol} staged value")
    return text


def _validate_vhyl(item: Mapping[str, object]) -> str:
    instrument = _required_mapping(item.get("instrument"), "VHYL.instrument")
    market = _required_mapping(item.get("market"), "VHYL.market")
    observed = _required_mapping(item.get("observation"), "VHYL.observation")
    if instrument.get("isin") != VHYL_ISIN:
        raise ValidationError("Staged VHYL ISIN is incorrect.")
    if market.get("mic") != VHYL_MIC or market.get("currency") != VHYL_CURRENCY:
        raise ValidationError("Staged VHYL market or currency is incorrect.")
    if observed.get("unit") != "EUR":
        raise ValidationError("Staged VHYL unit is incorrect.")
    return _verified_value(item, symbol="VHYL", expected_type="unadjusted_close")


def _validate_vwce(item: Mapping[str, object]) -> str:
    instrument = _required_mapping(item.get("instrument"), "VWCE.instrument")
    market = _required_mapping(item.get("market"), "VWCE.market")
    observed = _required_mapping(item.get("observation"), "VWCE.observation")
    if instrument.get("isin") != VWCE_ISIN:
        raise ValidationError("Staged VWCE ISIN is incorrect.")
    if market.get("mic") not in VWCE_VENUES or market.get("currency") != VWCE_CURRENCY:
        raise ValidationError("Staged VWCE market or currency is incorrect.")
    if observed.get("unit") != "EUR":
        raise ValidationError("Staged VWCE unit is incorrect.")
    return _verified_value(item, symbol="VWCE", expected_type="closing_auction_price")


def _validate_eurhuf(item: Mapping[str, object]) -> str:
    instrument = _required_mapping(item.get("instrument"), "EURHUF.instrument")
    market = _required_mapping(item.get("market"), "EURHUF.market")
    observed = _required_mapping(item.get("observation"), "EURHUF.observation")
    if instrument.get("seriesKey") != ECB_SERIES_KEY:
        raise ValidationError("Staged EUR/HUF series key is incorrect.")
    if market.get("mic") != "ECB" or market.get("currency") != "HUF":
        raise ValidationError("Staged EUR/HUF source or currency is incorrect.")
    if observed.get("unit") != "HUF_per_EUR":
        raise ValidationError("Staged EUR/HUF unit is incorrect.")
    return _verified_value(item, symbol="EURHUF", expected_type="official_reference_rate")


def _cash_number(value: object) -> int | float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValidationError("Latest history cashHUF must be a JSON number.")
    if value < 0:
        raise ValidationError("Latest history cashHUF cannot be negative.")
    return value


def _append_record_text(history_text: str, record: Mapping[str, object]) -> str:
    closing_index = history_text.rfind("]")
    if closing_index < 0 or history_text[closing_index + 1 :].strip():
        raise SourceFormatError("Portfolio history does not end with a JSON array bracket.")
    newline = "\r\n" if "\r\n" in history_text else "\n"
    prefix = history_text[:closing_index]
    if not prefix.endswith(newline):
        raise SourceFormatError("Portfolio history closing bracket must be on its own line.")

    record_lines = [
        "  {",
        f'    "date": {json.dumps(record["date"])},',
        f'    "VHYL": {record["VHYL"]},',
        f'    "VWCE": {record["VWCE"]},',
        f'    "EURHUF": {record["EURHUF"]},',
        f'    "cashHUF": {json.dumps(record["cashHUF"])}',
        "  }",
    ]
    without_final_newline = prefix[: -len(newline)]
    suffix = history_text[closing_index + 1 :]
    return (
        without_final_newline
        + ","
        + newline
        + newline.join(record_lines)
        + newline
        + "]"
        + suffix
    )


def plan_history_update(
    history_text: str,
    staging: Mapping[str, object],
    target_date: date,
) -> HistoryUpdatePlan:
    history = _history_records(history_text)
    existing_dates = [date.fromisoformat(str(item["date"])) for item in history]
    if target_date <= BASELINE_DATE:
        return HistoryUpdatePlan("skipped", "baseline or pre-baseline date is protected", target_date)
    if target_date in existing_dates:
        return HistoryUpdatePlan("skipped", "date already exists in history", target_date)
    if target_date <= existing_dates[-1]:
        return HistoryUpdatePlan(
            "skipped",
            "date is not later than the latest history record; existing records will not be reordered",
            target_date,
        )

    matches = _matching_observations(staging, target_date)
    missing = [symbol for symbol in ("VHYL", "VWCE", "EURHUF") if symbol not in matches]
    if missing:
        return HistoryUpdatePlan(
            "skipped", f"incomplete date; missing {', '.join(missing)}", target_date
        )
    try:
        vhyl = _validate_vhyl(matches["VHYL"])
        vwce = _validate_vwce(matches["VWCE"])
        eurhuf = _validate_eurhuf(matches["EURHUF"])
    except ValidationError as exc:
        return HistoryUpdatePlan("skipped", str(exc), target_date)

    record: dict[str, object] = {
        "date": target_date.isoformat(),
        "VHYL": vhyl,
        "VWCE": vwce,
        "EURHUF": eurhuf,
        "cashHUF": _cash_number(history[-1].get("cashHUF")),
    }
    updated_text = _append_record_text(history_text, record)
    updated = _history_records(updated_text)
    if updated[:-1] != history:
        raise ValidationError("History integration would alter an existing record.")
    expected_new = {
        "date": target_date.isoformat(),
        "VHYL": float(vhyl),
        "VWCE": float(vwce),
        "EURHUF": float(eurhuf),
        "cashHUF": record["cashHUF"],
    }
    if updated[-1] != expected_new:
        raise ValidationError("Appended history record failed round-trip validation.")
    return HistoryUpdatePlan(
        "ready", "complete verified date is ready to append", target_date, record, updated_text
    )


def write_history_atomic(path: Path, plan: HistoryUpdatePlan) -> None:
    if plan.status != "ready" or plan.updated_text is None:
        raise ValidationError("Only a ready history update plan may be written.")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
            handle.write(plan.updated_text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)
