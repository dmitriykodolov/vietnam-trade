"""Paginated catalogue discovery with raw-response preservation."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .client import VietnamCustomsClient, VietnamCustomsResponseError


@dataclass(frozen=True)
class DiscoveryResult:
    publications: list[dict[str, Any]]
    row_count: int
    pages_fetched: int
    duplicates_removed: int
    raw_directory: Path


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(path)


def discover_publications(
    client: VietnamCustomsClient,
    *,
    raw_directory: Path,
    take: int = 20,
    ky: str = "",
    text_search: str = "",
    the_loai: str = "0",
    thoigian_cong_bo: str = "",
    language: str = "TIENG_ANH",
) -> DiscoveryResult:
    """Fetch every page up to ``RowCount`` and deduplicate records by ``MA``."""
    if take <= 0:
        raise ValueError("take must be positive")

    raw_directory.mkdir(parents=True, exist_ok=True)
    by_ma: dict[str, dict[str, Any]] = {}
    duplicates = 0
    skip = 0
    pages = 0
    row_count: int | None = None

    filters = {
        "ky": ky,
        "text_search": text_search,
        "the_loai": the_loai,
        "thoigian_cong_bo": thoigian_cong_bo,
        "language": language,
    }

    while row_count is None or skip < row_count:
        page = client.fetch_page(skip=skip, take=take, **filters)
        _write_json(raw_directory / f"page_{skip:06d}.json", page)
        pages += 1

        try:
            current_count = int(page["RowCount"])
        except (TypeError, ValueError) as exc:
            raise VietnamCustomsResponseError("RowCount is not an integer") from exc
        if current_count < 0:
            raise VietnamCustomsResponseError("RowCount cannot be negative")
        if row_count is None:
            row_count = current_count
        else:
            row_count = max(row_count, current_count)

        records: Iterable[dict[str, Any]] = page.get("arr") or []
        records = list(records)
        for record in records:
            if not isinstance(record, dict):
                raise VietnamCustomsResponseError("Catalogue item is not an object")
            ma = record.get("MA")
            if ma is None or str(ma).strip() == "":
                raise VietnamCustomsResponseError("Catalogue item is missing MA")
            key = str(ma)
            if key in by_ma:
                duplicates += 1
            by_ma[key] = record

        skip += take
        if not records and skip < row_count:
            raise VietnamCustomsResponseError(
                f"Empty page at skip={skip - take} before RowCount={row_count}"
            )

    manifest = {
        "discovered_at": datetime.now(timezone.utc).isoformat(),
        "row_count": row_count or 0,
        "unique_publications": len(by_ma),
        "duplicates_removed": duplicates,
        "pages_fetched": pages,
        "take": take,
        "filters": filters,
    }
    _write_json(raw_directory / "manifest.json", manifest)
    return DiscoveryResult(
        publications=list(by_ma.values()),
        row_count=row_count or 0,
        pages_fetched=pages,
        duplicates_removed=duplicates,
        raw_directory=raw_directory,
    )


def load_raw_pages(raw_directory: Path) -> list[dict[str, Any]]:
    """Load saved pages and deduplicate records by MA, without network access."""
    by_ma: dict[str, dict[str, Any]] = {}
    for path in sorted(raw_directory.glob("page_*.json")):
        page = json.loads(path.read_text(encoding="utf-8"))
        for record in page.get("arr") or []:
            by_ma[str(record["MA"])] = record
    return list(by_ma.values())
