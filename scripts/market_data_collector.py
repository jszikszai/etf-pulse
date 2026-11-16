"""Source adapters and staging support for ETF Pulse market observations.

The module intentionally uses only the Python standard library.  It never reads
or writes the dashboard history as part of collection; callers can only write a
separate staging document.
"""

from __future__ import annotations

import csv
import gzip
import hashlib
import io
import json
import os
import tempfile
import time
from dataclasses import dataclass
from datetime import date, datetime, time as datetime_time, timedelta, timezone
from decimal import Decimal, InvalidOperation
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Callable, Iterable, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


BASELINE_DATE = date(2026, 11, 2)
VHYL_ISIN = "IE00B8GKDB10"
VHYL_MIC = "XAMS"
VHYL_CURRENCY = "EUR"
VWCE_ISIN = "IE00BK5BQT80"
VWCE_VENUES = frozenset({"XETA", "XETR"})
VWCE_CURRENCY = "EUR"
ECB_SERIES_KEY = "EXR.D.HUF.EUR.SP00.A"

RETRYABLE_HTTP_STATUSES = frozenset({429, 500, 502, 503, 504})


class CollectorError(Exception):
    """Base class for a safe, user-presentable collection failure."""


class ValidationError(CollectorError):
    pass


class IdentityError(ValidationError):
    pass


class SourceFormatError(ValidationError):
    pass


class MissingObservation(CollectorError):
    pass


class ArchiveUnavailable(CollectorError):
    pass


class TemporarySourceError(CollectorError):
    pass


class DuplicateObservationError(CollectorError):
    pass


@dataclass(frozen=True)
class FetchResult:
    source_url: str
    body: bytes


def parse_collection_date(value: str) -> date:
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise ValidationError(f"Invalid ISO date: {value!r}") from exc
    if parsed.isoformat() != value:
        raise ValidationError(f"Date must use YYYY-MM-DD: {value!r}")
    if parsed <= BASELINE_DATE:
        raise ValidationError(
            f"Collection date must be after immutable baseline {BASELINE_DATE.isoformat()}."
        )
    return parsed


def positive_decimal(raw: object, context: str) -> tuple[Decimal, str]:
    if isinstance(raw, bool) or raw is None:
        raise ValidationError(f"{context} must be a positive finite decimal.")
    text = str(raw).strip()
    try:
        value = Decimal(text)
    except (InvalidOperation, ValueError) as exc:
        raise ValidationError(f"{context} is not a decimal: {text!r}") from exc
    if not value.is_finite() or value <= 0:
        raise ValidationError(f"{context} must be positive and finite; found {text!r}.")
    return value, format(value, "f")


def utc_now_text(clock: Callable[[], datetime] | None = None) -> str:
    value = (clock or (lambda: datetime.now(timezone.utc)))()
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def observation_key(observation: Mapping[str, object]) -> str:
    instrument = observation.get("instrument")
    market = observation.get("market")
    observed = observation.get("observation")
    if not isinstance(instrument, Mapping) or not isinstance(market, Mapping) or not isinstance(observed, Mapping):
        raise SourceFormatError("Staged observation is missing identity fields.")
    instrument_identity = instrument.get("isin") or instrument.get("seriesKey")
    values = (
        instrument_identity,
        observed.get("date"),
        market.get("mic"),
    )
    if not all(isinstance(value, str) and value for value in values):
        raise SourceFormatError("Staged observation has an invalid identity key.")
    return "|".join(values)  # type: ignore[arg-type]


class HttpClient:
    """Small retrying HTTP client suitable for GitHub Actions."""

    def __init__(
        self,
        *,
        attempts: int = 4,
        timeout: float = 45.0,
        sleeper: Callable[[float], None] = time.sleep,
        opener: Callable[..., object] = urlopen,
    ) -> None:
        if attempts < 1:
            raise ValueError("attempts must be at least one")
        self.attempts = attempts
        self.timeout = timeout
        self.sleeper = sleeper
        self.opener = opener

    def open(self, url: str):
        last_error: Exception | None = None
        for attempt in range(self.attempts):
            request = Request(
                url,
                headers={
                    "User-Agent": "ETF-Pulse-Market-Collector/1.0",
                    "Accept": "*/*",
                },
            )
            try:
                return self.opener(request, timeout=self.timeout)
            except HTTPError as exc:
                if exc.code == 404:
                    raise ArchiveUnavailable(f"Source returned HTTP 404: {url}") from exc
                if exc.code not in RETRYABLE_HTTP_STATUSES:
                    raise CollectorError(f"Source returned HTTP {exc.code}: {url}") from exc
                last_error = exc
                if attempt + 1 < self.attempts:
                    self.sleeper(self._retry_delay(exc, attempt))
            except (URLError, TimeoutError, OSError) as exc:
                last_error = exc
                if attempt + 1 < self.attempts:
                    self.sleeper(float(2**attempt))
        raise TemporarySourceError(
            f"Source remained unavailable after {self.attempts} attempts: {url}. {last_error}"
        ) from last_error

    def get_bytes(self, url: str) -> FetchResult:
        response = self.open(url)
        try:
            body = response.read()
        finally:
            response.close()
        return FetchResult(url, body)

    @staticmethod
    def _retry_delay(error: HTTPError, attempt: int) -> float:
        retry_after = error.headers.get("Retry-After") if error.headers else None
        if retry_after:
            try:
                return min(float(retry_after), 60.0)
            except ValueError:
                try:
                    when = parsedate_to_datetime(retry_after)
                    now = datetime.now(timezone.utc)
                    return min(max((when - now).total_seconds(), 0.0), 60.0)
                except (TypeError, ValueError, OverflowError):
                    pass
        return float(2**attempt)


def euronext_url(target_date: date) -> str:
    path_identity = f"{VHYL_ISIN}-{VHYL_MIC}"
    query = urlencode(
        {
            "format": "csv",
            "decimal_separator": ".",
            "date_form": "d/m/Y",
            "startdate": target_date.isoformat(),
            "enddate": target_date.isoformat(),
        }
    )
    return (
        "https://live.euronext.com/en/ajax/AwlHistoricalPrice/"
        f"getFullDownloadAjax/{path_identity}?{query}"
    )


def parse_euronext_csv(
    text: str,
    target_date: date,
    *,
    source_url: str,
    retrieved_at: str,
) -> dict[str, object]:
    expected_path_identity = f"/{VHYL_ISIN}-{VHYL_MIC}?"
    if expected_path_identity not in source_url:
        raise IdentityError("Euronext source URL does not identify the required XAMS listing.")

    lines = [line.strip("\r") for line in text.lstrip("\ufeff").splitlines() if line.strip()]
    if VHYL_ISIN not in {line.strip().strip('"') for line in lines}:
        raise IdentityError(f"Euronext CSV does not identify expected ISIN {VHYL_ISIN}.")

    header_index = next((i for i, line in enumerate(lines) if line.startswith("Date;")), None)
    if header_index is None:
        raise SourceFormatError("Euronext CSV has no historical-data header.")
    header = next(csv.reader([lines[header_index]], delimiter=";"))
    try:
        date_index = header.index("Date")
        close_index = header.index("Close")
    except ValueError as exc:
        raise SourceFormatError("Euronext CSV lacks Date or Close fields.") from exc

    matches: list[list[str]] = []
    for line in lines[header_index + 1 :]:
        row = next(csv.reader([line], delimiter=";"))
        if len(row) <= max(date_index, close_index):
            raise SourceFormatError("Euronext CSV contains a short data row.")
        try:
            row_date = datetime.strptime(row[date_index], "%d/%m/%Y").date()
        except ValueError as exc:
            raise SourceFormatError(f"Invalid Euronext trading date: {row[date_index]!r}") from exc
        if row_date == target_date:
            matches.append(row)

    if not matches:
        raise MissingObservation(f"Euronext has no VHYL record for {target_date.isoformat()}.")
    if len(matches) != 1:
        raise SourceFormatError("Euronext returned duplicate rows for the requested date.")

    _, close_text = positive_decimal(matches[0][close_index], "Euronext VHYL close")
    return {
        "instrument": {"symbol": "VHYL", "isin": VHYL_ISIN},
        "market": {
            "name": "Euronext Amsterdam",
            "mic": VHYL_MIC,
            "currency": VHYL_CURRENCY,
        },
        "observation": {
            "date": target_date.isoformat(),
            "value": close_text,
            "unit": "EUR",
            "type": "unadjusted_close",
        },
        "verification": {
            "status": "verified",
            "method": "official_euronext_historical_close_field",
        },
        "provenance": {
            "publisher": "Euronext",
            "sourceUrl": source_url,
            "retrievedAt": retrieved_at,
        },
    }


def ecb_url(target_date: date) -> str:
    query = urlencode(
        {
            "startPeriod": target_date.isoformat(),
            "endPeriod": target_date.isoformat(),
            "format": "csvdata",
        }
    )
    return f"https://data-api.ecb.europa.eu/service/data/EXR/D.HUF.EUR.SP00.A?{query}"


def parse_ecb_csv(
    text: str,
    target_date: date,
    *,
    source_url: str,
    retrieved_at: str,
) -> dict[str, object]:
    reader = csv.DictReader(io.StringIO(text.lstrip("\ufeff")))
    required = {"KEY", "FREQ", "CURRENCY", "CURRENCY_DENOM", "TIME_PERIOD", "OBS_VALUE"}
    if reader.fieldnames is None or not required.issubset(reader.fieldnames):
        raise SourceFormatError("ECB CSV lacks required series identity fields.")

    target_rows: list[dict[str, str]] = []
    for row in reader:
        if row.get("TIME_PERIOD") == target_date.isoformat():
            target_rows.append(row)
    if not target_rows:
        raise MissingObservation(f"ECB has no EUR/HUF reference rate for {target_date.isoformat()}.")
    if len(target_rows) != 1:
        raise SourceFormatError("ECB returned duplicate observations for the requested date.")

    row = target_rows[0]
    expected = {
        "KEY": ECB_SERIES_KEY,
        "FREQ": "D",
        "CURRENCY": "HUF",
        "CURRENCY_DENOM": "EUR",
    }
    for field, value in expected.items():
        if row.get(field) != value:
            raise IdentityError(
                f"ECB {field} mismatch: expected {value!r}, found {row.get(field)!r}."
            )
    _, rate_text = positive_decimal(row.get("OBS_VALUE"), "ECB EUR/HUF rate")
    return {
        "instrument": {"symbol": "EURHUF", "seriesKey": ECB_SERIES_KEY},
        "market": {"name": "European Central Bank", "mic": "ECB", "currency": "HUF"},
        "observation": {
            "date": target_date.isoformat(),
            "value": rate_text,
            "unit": "HUF_per_EUR",
            "type": "official_reference_rate",
        },
        "verification": {"status": "verified", "method": "official_ecb_data_api"},
        "provenance": {
            "publisher": "European Central Bank",
            "sourceUrl": source_url,
            "retrievedAt": retrieved_at,
        },
    }


def xetra_daily_url(target_date: date) -> str:
    return (
        "https://mfs.deutsche-boerse.com/api/download/"
        f"DETR-posttrade-daily-{target_date.isoformat()}.json.gz"
    )


def _last_sunday(year: int, month: int) -> date:
    if month == 12:
        next_month = date(year + 1, 1, 1)
    else:
        next_month = date(year, month + 1, 1)
    candidate = next_month - timedelta(days=1)
    return candidate - timedelta(days=(candidate.weekday() + 1) % 7)


def berlin_local_datetime(utc_value: datetime) -> datetime:
    """Convert UTC to Europe/Berlin using the EU DST rule, without tzdata."""

    if utc_value.tzinfo is None:
        raise ValidationError("Xetra timestamp must include a UTC offset.")
    utc_value = utc_value.astimezone(timezone.utc)
    year = utc_value.year
    dst_start = datetime.combine(_last_sunday(year, 3), datetime_time(1), timezone.utc)
    dst_end = datetime.combine(_last_sunday(year, 10), datetime_time(1), timezone.utc)
    offset = timedelta(hours=2 if dst_start <= utc_value < dst_end else 1)
    return (utc_value + offset).replace(tzinfo=timezone(offset))


def parse_xetra_timestamp(raw: object) -> tuple[datetime, datetime]:
    if not isinstance(raw, str) or not raw:
        raise SourceFormatError("Xetra record lacks tradingDateAndTime.")
    normalized = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
    # datetime.fromisoformat accepts at most six fractional digits on older Python
    if "." in normalized:
        prefix, suffix = normalized.split(".", 1)
        tz_pos = max(suffix.find("+"), suffix.find("-"))
        if tz_pos >= 0:
            fraction, offset = suffix[:tz_pos], suffix[tz_pos:]
        else:
            fraction, offset = suffix, ""
        normalized = f"{prefix}.{fraction[:6]}{offset}"
    try:
        utc_value = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise SourceFormatError(f"Invalid Xetra timestamp: {raw!r}") from exc
    if utc_value.tzinfo is None:
        raise SourceFormatError("Xetra timestamp does not identify a timezone.")
    utc_value = utc_value.astimezone(timezone.utc)
    return utc_value, berlin_local_datetime(utc_value)


def parse_xetra_records(
    lines: Iterable[str],
    target_date: date,
    *,
    source_url: str,
    retrieved_at: str,
) -> dict[str, object]:
    expected_isin_seen = False
    candidates: list[dict[str, object]] = []

    for line_number, raw_line in enumerate(lines, start=1):
        line = raw_line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise SourceFormatError(f"Invalid Xetra NDJSON on line {line_number}.") from exc
        if not isinstance(record, dict):
            raise SourceFormatError(f"Xetra line {line_number} is not an object.")

        timestamp_raw = record.get("tradingDateAndTime")
        if not isinstance(timestamp_raw, str):
            continue
        _, local_time = parse_xetra_timestamp(timestamp_raw)
        if local_time.date() != target_date:
            continue
        if record.get("instrumentIdentificationCode") != VWCE_ISIN:
            continue

        expected_isin_seen = True
        if record.get("priceCurrency") != VWCE_CURRENCY:
            raise IdentityError("VWCE post-trade record is not denominated in EUR.")
        if record.get("venueOfExecution") not in VWCE_VENUES:
            raise IdentityError(
                f"VWCE post-trade record has unexpected venue {record.get('venueOfExecution')!r}."
            )
        if record.get("tradingSystem") != "CLOB":
            continue

        local_clock = local_time.timetz().replace(tzinfo=None)
        # Xetra's closing auction call phase starts at 17:30, but the official
        # schedule fixes the ETF closing-auction price from 17:35.  The real
        # feed can contain residual regular CLOB executions shortly after
        # 17:30, so those must not be mistaken for the auction uncrossing.
        if not (datetime_time(17, 35) <= local_clock <= datetime_time(18, 0)):
            continue
        price, price_text = positive_decimal(record.get("price"), "Xetra VWCE trade price")
        quantity, quantity_text = positive_decimal(record.get("quantity"), "Xetra VWCE quantity")
        candidates.append(
            {
                "local": local_time,
                "utc": timestamp_raw,
                "price": price,
                "price_text": price_text,
                "quantity": quantity,
                "quantity_text": quantity_text,
                "venue": record.get("venueOfExecution"),
            }
        )

    if not expected_isin_seen:
        raise MissingObservation(
            f"Deutsche Boerse archive has no {VWCE_ISIN} trades for {target_date.isoformat()}."
        )
    if not candidates:
        raise MissingObservation(
            f"No regular VWCE CLOB print was found in the Xetra closing window for {target_date.isoformat()}."
        )

    candidates.sort(key=lambda item: item["local"])
    selected = candidates[0]
    selected_local = selected["local"]
    assert isinstance(selected_local, datetime)
    selected_price = selected["price"]
    assert isinstance(selected_price, Decimal)

    trade_at_close_end = datetime_time(17, 40)
    closing_prices = {
        item["price"]
        for item in candidates
        if isinstance(item["local"], datetime)
        and item["local"].timetz().replace(tzinfo=None) <= trade_at_close_end
    }
    scheduled_window = datetime_time(17, 35) <= selected_local.timetz().replace(tzinfo=None) <= datetime_time(17, 45)
    unambiguous = closing_prices == {selected_price}
    verified = scheduled_window and unambiguous
    status = "verified" if verified else "unverified"
    if not scheduled_window:
        reason = "first regular CLOB print is outside the expected 17:35-17:45 closing-auction window"
    elif not unambiguous:
        reason = "regular CLOB prints in the closing/Trade-at-Close window contain conflicting prices"
    else:
        reason = (
            "inferred from the first regular Xetra CLOB print in the official closing-auction window; "
            "the delayed feed has no explicit auction flag"
        )

    return {
        "instrument": {"symbol": "VWCE", "isin": VWCE_ISIN},
        "market": {"name": "Xetra", "mic": selected["venue"], "currency": VWCE_CURRENCY},
        "observation": {
            "date": target_date.isoformat(),
            "value": selected["price_text"],
            "unit": "EUR",
            "type": "closing_auction_price" if verified else "candidate_closing_auction_price",
        },
        "verification": {
            "status": status,
            "method": "xetra_regular_clob_market_phase_selection",
            "reason": reason,
        },
        "auctionCandidate": {
            "tradingDateTime": selected["utc"],
            "localTime": selected_local.isoformat(),
            "quantity": selected["quantity_text"],
            "regularClosingWindowPrices": sorted(format(value, "f") for value in closing_prices),
        },
        "provenance": {
            "publisher": "Deutsche Boerse",
            "sourceUrl": source_url,
            "retrievedAt": retrieved_at,
        },
    }


def issue(
    *,
    symbol: str,
    target_date: date,
    source: str,
    source_url: str,
    status: str,
    reason: str,
) -> dict[str, str]:
    return {
        "instrument": symbol,
        "date": target_date.isoformat(),
        "source": source,
        "sourceUrl": source_url,
        "status": status,
        "reason": reason,
    }


def _classify_issue(exc: CollectorError) -> str:
    if isinstance(exc, MissingObservation):
        return "missing_market_date"
    if isinstance(exc, ArchiveUnavailable):
        return "archive_unavailable"
    if isinstance(exc, TemporarySourceError):
        return "temporary_failure"
    return "validation_failure"


def collect_date(
    target_date: date,
    *,
    http: HttpClient | None = None,
    clock: Callable[[], datetime] | None = None,
) -> dict[str, object]:
    if target_date <= BASELINE_DATE:
        raise ValidationError("Collector refuses to retrieve or stage the immutable baseline date.")
    client = http or HttpClient()
    retrieved_at = utc_now_text(clock)
    observations: list[dict[str, object]] = []
    issues: list[dict[str, str]] = []

    vhyl_source = euronext_url(target_date)
    try:
        fetched = client.get_bytes(vhyl_source)
        observations.append(
            parse_euronext_csv(
                fetched.body.decode("utf-8-sig"),
                target_date,
                source_url=fetched.source_url,
                retrieved_at=retrieved_at,
            )
        )
    except (CollectorError, UnicodeDecodeError) as exc:
        wrapped = exc if isinstance(exc, CollectorError) else SourceFormatError(str(exc))
        issues.append(
            issue(
                symbol="VHYL",
                target_date=target_date,
                source="Euronext",
                source_url=vhyl_source,
                status=_classify_issue(wrapped),
                reason=str(wrapped),
            )
        )

    vwce_source = xetra_daily_url(target_date)
    try:
        response = client.open(vwce_source)
        try:
            with gzip.GzipFile(fileobj=response) as compressed:
                with io.TextIOWrapper(compressed, encoding="utf-8") as text_stream:
                    observations.append(
                        parse_xetra_records(
                            text_stream,
                            target_date,
                            source_url=vwce_source,
                            retrieved_at=retrieved_at,
                        )
                    )
        finally:
            response.close()
    except (CollectorError, OSError, UnicodeDecodeError) as exc:
        wrapped = exc if isinstance(exc, CollectorError) else SourceFormatError(str(exc))
        issues.append(
            issue(
                symbol="VWCE",
                target_date=target_date,
                source="Deutsche Boerse",
                source_url=vwce_source,
                status=_classify_issue(wrapped),
                reason=str(wrapped),
            )
        )

    fx_source = ecb_url(target_date)
    try:
        fetched = client.get_bytes(fx_source)
        observations.append(
            parse_ecb_csv(
                fetched.body.decode("utf-8-sig"),
                target_date,
                source_url=fetched.source_url,
                retrieved_at=retrieved_at,
            )
        )
    except (CollectorError, UnicodeDecodeError) as exc:
        wrapped = exc if isinstance(exc, CollectorError) else SourceFormatError(str(exc))
        issues.append(
            issue(
                symbol="EURHUF",
                target_date=target_date,
                source="European Central Bank",
                source_url=fx_source,
                status=_classify_issue(wrapped),
                reason=str(wrapped),
            )
        )

    return {
        "schemaVersion": 1,
        "collectionDate": target_date.isoformat(),
        "generatedAt": retrieved_at,
        "observations": observations,
        "issues": issues,
    }


def merge_staging(existing: Mapping[str, object] | None, collected: Mapping[str, object]) -> dict[str, object]:
    existing_observations = [] if existing is None else existing.get("observations", [])
    existing_issues = [] if existing is None else existing.get("issues", [])
    new_observations = collected.get("observations", [])
    new_issues = collected.get("issues", [])
    if not isinstance(existing_observations, list) or not isinstance(existing_issues, list):
        raise SourceFormatError("Existing staging document has invalid arrays.")
    if not isinstance(new_observations, list) or not isinstance(new_issues, list):
        raise SourceFormatError("Collected document has invalid arrays.")

    known: set[str] = set()
    for item in existing_observations:
        if not isinstance(item, Mapping):
            raise SourceFormatError("Existing staging observation is not an object.")
        key = observation_key(item)
        if key in known:
            raise DuplicateObservationError(f"Existing staging file already contains duplicate {key}.")
        known.add(key)
    for item in new_observations:
        if not isinstance(item, Mapping):
            raise SourceFormatError("Collected observation is not an object.")
        key = observation_key(item)
        if key in known:
            raise DuplicateObservationError(
                f"Refusing to overwrite existing staged observation {key}."
            )
        known.add(key)

    return {
        "schemaVersion": 1,
        "generatedAt": collected.get("generatedAt"),
        "observations": [*existing_observations, *new_observations],
        "issues": [*existing_issues, *new_issues],
    }


def read_staging(path: Path) -> dict[str, object] | None:
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SourceFormatError(f"Cannot read staging JSON {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise SourceFormatError("Staging JSON root must be an object.")
    return value


def assert_safe_staging_path(path: Path, repository_root: Path) -> None:
    resolved = path.resolve()
    protected = {
        (repository_root / "index.html").resolve(),
        (repository_root / "data" / "history.json").resolve(),
        (repository_root / "data" / "baseline.json").resolve(),
    }
    if resolved in protected:
        raise ValidationError(f"Refusing to write protected project file: {resolved}")


def protected_hashes(repository_root: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for relative in ("index.html", "data/history.json", "data/baseline.json"):
        path = repository_root / relative
        result[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def write_staging(path: Path, document: Mapping[str, object], repository_root: Path) -> None:
    assert_safe_staging_path(path, repository_root)
    before = protected_hashes(repository_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(document, ensure_ascii=False, indent=2) + "\n"
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)
    after = protected_hashes(repository_root)
    if before != after:
        raise ValidationError("A protected project file changed during staging write.")
