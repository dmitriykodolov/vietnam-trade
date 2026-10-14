"""Deterministic selection of one document per series and reporting year."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable

from .downloader import (
    DownloadCandidate,
    document_destination,
    validate_download_url,
)
from .registry import CORE_SERIES


EDITION_PRIORITY = ("final", "revised", "preliminary")
SELECTION_RULE_VERSION = "v1"


@dataclass(frozen=True)
class SelectionRecord:
    series: str
    reference_year: int
    publication_period: str | None
    ma: str | None
    edition: str | None
    source_url: str | None
    local_path: str | None
    file_exists: bool
    is_complete_year: bool
    coverage_complete: bool
    edition_fallback: bool
    fallback_reason: str | None
    selection_rule_version: str = SELECTION_RULE_VERSION


def _matching_editions(
    publication: dict[str, Any], series: str
) -> list[tuple[str, str]]:
    matches: list[tuple[str, str]] = []
    editions = publication.get("editions") or {}
    for edition in EDITION_PRIORITY:
        item = editions.get(edition) or {}
        url = item.get("source_url")
        allowed, _ = validate_download_url(url)
        if item.get("series") == series and allowed:
            matches.append((edition, url))
    return matches


def select_annual_documents(
    registry: Iterable[dict[str, Any]],
    *,
    documents_directory: Path,
    start: str = "2017-01",
    end: str = "2026-09",
    series: Iterable[str] = CORE_SERIES,
) -> list[SelectionRecord]:
    """Select the best available edition for every series/year.

    Closed years prefer December.  The end year prefers the latest available
    month.  Within the chosen month, edition priority is final, revised, then
    preliminary.  If December is unavailable, an earlier month is selected and
    explicitly marked as incomplete coverage.
    """
    if start > end:
        raise ValueError("start must not be after end")
    start_year, end_year = int(start[:4]), int(end[:4])
    publications = list(registry)
    selected: list[SelectionRecord] = []

    for series_code in sorted(set(series)):
        for year in range(start_year, end_year + 1):
            is_complete_year = year < end_year
            year_items = [
                item
                for item in publications
                if item.get("publication_period")
                and item["publication_period"].startswith(f"{year:04d}-")
                and start <= item["publication_period"] <= end
                and _matching_editions(item, series_code)
            ]
            year_items.sort(
                key=lambda item: (
                    item["publication_period"],
                    int(item["ma"]) if str(item["ma"]).isdigit() else -1,
                ),
                reverse=True,
            )

            chosen: tuple[dict[str, Any], str, str] | None = None
            preferred_period = f"{year:04d}-12" if is_complete_year else None
            ordered = year_items
            if preferred_period:
                ordered = sorted(
                    year_items,
                    key=lambda item: item["publication_period"] == preferred_period,
                    reverse=True,
                )
            for publication in ordered:
                matches = _matching_editions(publication, series_code)
                if matches:
                    edition, url = matches[0]
                    chosen = publication, edition, url
                    break

            if chosen is None:
                selected.append(
                    SelectionRecord(
                        series=series_code,
                        reference_year=year,
                        publication_period=None,
                        ma=None,
                        edition=None,
                        source_url=None,
                        local_path=None,
                        file_exists=False,
                        is_complete_year=is_complete_year,
                        coverage_complete=False,
                        edition_fallback=True,
                        fallback_reason="no_public_document",
                    )
                )
                continue

            publication, edition, url = chosen
            period = publication["publication_period"]
            ma = str(publication["ma"])
            path = document_destination(
                documents_directory,
                series=series_code,
                period=period,
                ma=ma,
                edition=edition,
                url=url,
            )
            reasons: list[str] = []
            coverage_complete = is_complete_year and period.endswith("-12")
            if is_complete_year and not coverage_complete:
                reasons.append("december_missing")
            if not is_complete_year:
                reasons.append("current_year_latest_period")
            if edition != "final":
                reasons.append("final_unavailable")
            selected.append(
                SelectionRecord(
                    series=series_code,
                    reference_year=year,
                    publication_period=period,
                    ma=ma,
                    edition=edition,
                    source_url=url,
                    local_path=str(path),
                    file_exists=path.exists(),
                    is_complete_year=is_complete_year,
                    coverage_complete=coverage_complete,
                    edition_fallback=edition != "final",
                    fallback_reason=",".join(reasons) or None,
                )
            )
    return selected


def write_selection(path: Path, records: Iterable[SelectionRecord]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        for record in records:
            stream.write(json.dumps(asdict(record), ensure_ascii=False) + "\n")
    temporary.replace(path)


def read_selection(path: Path) -> list[SelectionRecord]:
    records: list[SelectionRecord] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            records.append(SelectionRecord(**json.loads(line)))
    return records


def selection_download_candidates(
    records: Iterable[SelectionRecord],
) -> list[DownloadCandidate]:
    candidates: list[DownloadCandidate] = []
    for item in records:
        if not all(
            (item.ma, item.publication_period, item.edition, item.source_url, item.local_path)
        ):
            continue
        candidates.append(
            DownloadCandidate(
                key=f"{item.ma}:{item.edition}:{item.series}",
                ma=item.ma,
                series=item.series,
                period=item.publication_period,
                edition=item.edition,
                url=item.source_url,
                destination=Path(item.local_path),
            )
        )
    return candidates
