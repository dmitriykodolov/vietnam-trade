"""Normalisation and registry generation for Vietnam Customs publications."""

from __future__ import annotations

import ipaddress
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import unquote, urlsplit


CORE_SERIES = frozenset({"2X", "2N", "5X", "5N"})
ADDITIONAL_SERIES = frozenset({"3X", "3N"})
DEFERRED_SERIES = frozenset({"4", "1X", "1N"})
KNOWN_SERIES = CORE_SERIES | ADDITIONAL_SERIES | DEFERRED_SERIES
SERIES_PATTERN = re.compile(
    r"(?i)(?:^|[-_])(1X|1N|2X|2N|3X|3N|4|5X|5N)(?=[(._-]|$)"
)
EDITION_FIELDS = {
    "preliminary": ("NGAY_SO_BO", "FILE_SO_BO"),
    "revised": ("NGAY_DIEU_CHINH", "FILE_DIEU_CHINH"),
    "final": ("NGAY_CHINH_THUC", "FILE_CHINH_THUC"),
}


def normalize_nulls(value: Any) -> Any:
    """Recursively turn blank/null-like strings into Python ``None``."""
    if isinstance(value, str):
        stripped = value.strip()
        return None if not stripped or stripped.lower() == "null" else stripped
    if isinstance(value, list):
        return [normalize_nulls(item) for item in value]
    if isinstance(value, dict):
        return {key: normalize_nulls(item) for key, item in value.items()}
    return value


def is_internal_10_url(url: str | None) -> bool:
    """Return true for an HTTP(S) URL whose literal host is in 10.0.0.0/8."""
    if not url:
        return False
    parsed = urlsplit(url)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        return False
    try:
        address = ipaddress.ip_address(parsed.hostname)
    except ValueError:
        return False
    return address.version == 4 and address in ipaddress.ip_network("10.0.0.0/8")


def safe_download_url(url: str | None) -> str | None:
    """Return a downloadable public HTTP(S) URL, or ``None`` when disallowed."""
    if not url or is_internal_10_url(url):
        return None
    parsed = urlsplit(url)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        return None
    return url


def series_from_url(url: str | None) -> str | None:
    if not url:
        return None
    filename = unquote(Path(urlsplit(url).path).name)
    match = SERIES_PATTERN.search(filename)
    return match.group(1).upper() if match else None


def parse_period(value: Any) -> str | None:
    """Normalise the catalogue period to YYYY-MM when possible."""
    if value is None:
        return None
    text = str(value).strip()
    for pattern in (r"^(\d{2})[-/](\d{4})$", r"^(\d{4})[-/](\d{2})$"):
        match = re.match(pattern, text)
        if match:
            if pattern.startswith("^(\\d{2})"):
                month, year = int(match.group(1)), int(match.group(2))
            else:
                year, month = int(match.group(1)), int(match.group(2))
            if 1900 <= year <= 2100 and 1 <= month <= 12:
                return f"{year:04d}-{month:02d}"
    return None


def normalize_publication(record: dict[str, Any]) -> dict[str, Any]:
    source = normalize_nulls(record)
    editions: dict[str, dict[str, Any]] = {}
    detected_series: set[str] = set()
    for edition, (date_field, url_field) in EDITION_FIELDS.items():
        source_url = source.get(url_field)
        series = series_from_url(source_url)
        if series:
            detected_series.add(series)
        editions[edition] = {
            "date": source.get(date_field),
            "source_url": source_url,
            "download_url": safe_download_url(source_url),
            "blocked_internal_url": is_internal_10_url(source_url),
            "series": series,
        }

    return {
        "ma": str(source["MA"]),
        "title": source.get("TIEU_DE"),
        "publication_period": parse_period(source.get("NGAY_CONG_BO")),
        "publication_period_raw": source.get("NGAY_CONG_BO"),
        "language": source.get("NGON_NGU"),
        "author": source.get("TAC_GIA"),
        "edition_labels_raw": source.get("TEN_THE_LOAI"),
        "edition_codes_raw": source.get("THE_LOAI"),
        "series": sorted(detected_series),
        "editions": editions,
        "source": source,
    }


def build_registry(records: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Normalise and deduplicate publications by MA."""
    registry = {str(item["MA"]): normalize_publication(item) for item in records}
    return sorted(
        registry.values(),
        key=lambda item: (
            0 if item["ma"].isdigit() else 1,
            int(item["ma"]) if item["ma"].isdigit() else item["ma"],
        ),
    )


def filter_target_series(
    registry: Iterable[dict[str, Any]],
    *,
    start: str = "2023-01",
    end: str = "2026-09",
) -> list[dict[str, Any]]:
    """Select 2X/2N/5X/5N publications in the inclusive YYYY-MM window."""
    datetime.strptime(start, "%Y-%m")
    datetime.strptime(end, "%Y-%m")
    if start > end:
        raise ValueError("start must not be after end")
    return [
        item
        for item in registry
        if item.get("publication_period")
        and start <= item["publication_period"] <= end
        and CORE_SERIES.intersection(item.get("series") or [])
    ]


def write_registry(path: Path, records: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": datetime.now().astimezone().isoformat(),
        "publications": list(records),
    }
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(path)
