"""Coordinate-based parser for Vietnam Customs 5X/5N PDF tables."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from decimal import Decimal
from pathlib import Path
from typing import Any

import pdfplumber

from .parser_summary import (
    TABLE_SETTINGS,
    ParseResult,
    _extract_header,
    _sha256,
    _write_json,
    _write_jsonl,
    parse_number,
)
from .selector import SelectionRecord

PARSER_VERSION = "country-v1"
TABLE_CODES = {
    "5X": frozenset({"19B/TCHQ", "5X/TCHQ"}),
    "5N": frozenset({"20B/TCHQ", "5N/TCHQ"}),
}


def _clean(value: Any) -> str | None:
    if value is None:
        return None
    text = " ".join(str(value).split())
    return text or None


def _cluster_words(words: list[dict[str, Any]], tolerance: float = 2.0):
    groups: list[list[dict[str, Any]]] = []
    for word in sorted(words, key=lambda item: (item["top"], item["x0"])):
        if not groups or abs(word["top"] - groups[-1][0]["top"]) > tolerance:
            groups.append([word])
        else:
            groups[-1].append(word)
    return groups


def extract_visual_lines(page) -> tuple[list[dict[str, Any]], str | None]:
    """Return six-column visual lines from the main table on one page."""
    tables = page.find_tables(TABLE_SETTINGS)
    if not tables:
        return [], "table_missing"
    table = max(tables, key=lambda item: item.bbox[2] - item.bbox[0])
    boundaries = sorted(
        {round(value, 3) for cell in table.cells for value in (cell[0], cell[2])}
    )
    if len(boundaries) != 7:
        return [], f"unexpected_column_boundaries_{len(boundaries)}"

    horizontal = sorted(
        {round(value, 3) for cell in table.cells for value in (cell[1], cell[3])}
    )
    if len(horizontal) < 3:
        return [], "table_header_missing"
    data_top = horizontal[2]
    bbox = table.bbox
    words = page.extract_words(
        keep_blank_chars=False, extra_attrs=["fontname", "size"]
    )
    words = [
        word
        for word in words
        if word["top"] >= data_top - 1
        # pdfplumber often closes the detected table at an intermediate
        # horizontal rule even though the printed rows continue to the footer.
        # Keep the detected columns/header, but read the full data area.
        and word["bottom"] <= page.height - 20
        and word["x0"] >= bbox[0] - 1
        and word["x1"] <= bbox[2] + 1
    ]

    lines: list[dict[str, Any]] = []
    for group in _cluster_words(words):
        columns: list[list[dict[str, Any]]] = [[] for _ in range(6)]
        for word in group:
            center = (word["x0"] + word["x1"]) / 2
            for index in range(6):
                if boundaries[index] <= center < boundaries[index + 1] + 0.01:
                    columns[index].append(word)
                    break
        values = [
            _clean(" ".join(word["text"] for word in sorted(column, key=lambda w: w["x0"])))
            for column in columns
        ]
        if not any(values):
            continue
        lines.append(
            {
                "top": min(word["top"] for word in group),
                "bottom": max(word["bottom"] for word in group),
                "values": values,
                "name_is_bold": bool(columns[0])
                and all("bold" in word["fontname"].lower() for word in columns[0]),
            }
        )
    return lines, None


def _numeric_fields(values: list[str | None], warnings: list[str]) -> dict[str, Any]:
    names = (
        "reporting_month_volume",
        "reporting_month_value_usd",
        "ytd_volume",
        "ytd_value_usd",
    )
    result: dict[str, Any] = {}
    for offset, name in enumerate(names, start=2):
        result[f"{name}_raw"] = values[offset]
        try:
            result[name] = parse_number(values[offset])
        except ValueError:
            result[name] = None
            warnings.append(f"invalid_{name}")
    return result


def parse_visual_lines(
    lines: Iterable[dict[str, Any]],
    *,
    document_id: str,
    page_number: int,
    state: dict[str, Any],
    rows: list[dict[str, Any]],
    warnings: list[str],
) -> None:
    """Parse visual lines while carrying the active country across pages."""
    for line in lines:
        values = line["values"]
        name, unit = values[:2]
        if not name and not any(values[1:]):
            continue
        if any(value and value.lower().startswith("page") for value in values):
            continue
        if name and any(
            marker in name.lower()
            for marker in ("novapdf", "create pdf files without")
        ):
            continue

        has_numbers = any(values[2:])
        if line["name_is_bold"] and name and not unit:
            state["country_group_number"] += 1
            state["country"] = name
            state["country_header_page"] = page_number
            row_warnings: list[str] = []
            row = {
                "document_id": document_id,
                "page_number": page_number,
                "source_row_number": len(rows) + 1,
                "source_line_top": round(float(line["top"]), 3),
                "row_type": "country_total",
                "country_group_number": state["country_group_number"],
                "country_name_raw": name,
                "commodity_name_raw": None,
                "unit_raw": None,
                "country_inherited": False,
                "country_header_page": page_number,
                "raw_cells": values,
                "parse_warnings": row_warnings,
            }
            row.update(_numeric_fields(values, row_warnings))
            rows.append(row)
            state["last_row"] = row
            continue

        if unit:
            row_warnings = []
            if not state.get("country"):
                row_warnings.append("country_missing")
                warnings.append(f"page_{page_number}_orphan_commodity")
            row = {
                "document_id": document_id,
                "page_number": page_number,
                "source_row_number": len(rows) + 1,
                "source_line_top": round(float(line["top"]), 3),
                "row_type": "commodity",
                "country_group_number": state.get("country_group_number"),
                "country_name_raw": state.get("country"),
                "commodity_name_raw": name,
                "unit_raw": unit,
                "country_inherited": state.get("country_header_page") != page_number,
                "country_header_page": state.get("country_header_page"),
                "raw_cells": values,
                "parse_warnings": row_warnings,
            }
            row.update(_numeric_fields(values, row_warnings))
            rows.append(row)
            state["last_row"] = row
            continue

        if name and not has_numbers and state.get("last_row"):
            previous = state["last_row"]
            if previous["row_type"] == "commodity":
                previous["commodity_name_raw"] = (
                    f"{previous['commodity_name_raw']} {name}"
                )
                previous["raw_cells"][0] = previous["commodity_name_raw"]
            elif previous["row_type"] == "country_total":
                previous["country_name_raw"] = f"{previous['country_name_raw']} {name}"
                previous["raw_cells"][0] = previous["country_name_raw"]
                state["country"] = previous["country_name_raw"]
            continue

        warnings.append(f"page_{page_number}_unclassified_line_{line['top']:.1f}")


def _country_value_checks(rows: list[dict[str, Any]]) -> dict[str, Any]:
    groups: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if row.get("country_group_number") is not None:
            groups[row["country_group_number"]].append(row)
    mismatches: list[dict[str, Any]] = []
    checked = 0
    for group_rows in groups.values():
        total = next(
            (row for row in group_rows if row["row_type"] == "country_total"), None
        )
        commodities = [row for row in group_rows if row["row_type"] == "commodity"]
        if not total or not commodities:
            continue
        # Legacy tables (notably 2017) list only selected commodities.  A
        # country total can be reconciled only when the balancing row exists.
        if not any(
            (row.get("commodity_name_raw") or "").lower().startswith("other product")
            for row in commodities
        ):
            continue
        for field in ("reporting_month_value_usd", "ytd_value_usd"):
            if total.get(field) is None:
                continue
            checked += 1
            expected = Decimal(total[field])
            actual = sum(
                (Decimal(row[field]) for row in commodities if row.get(field) is not None),
                Decimal(0),
            )
            # Published detail rows are rounded independently; all observed
            # reconciliations differ by at most five dollars.
            if abs(expected - actual) > Decimal(5):
                mismatches.append(
                    {
                        "country": total["country_name_raw"],
                        "field": field,
                        "country_total": str(expected),
                        "commodity_sum": str(actual),
                        "difference": str(expected - actual),
                    }
                )
    return {
        "checked": checked,
        "mismatch_count": len(mismatches),
        "mismatch_examples": mismatches[:20],
    }


def parse_country_pdf(
    pdf_path: Path,
    *,
    series: str,
    ma: str,
    edition: str,
    expected_period: str,
    source_url: str,
    output_directory: Path,
) -> ParseResult:
    if series not in TABLE_CODES:
        raise ValueError("country parser supports only 5X and 5N")
    document_id = _sha256(pdf_path)
    pages: list[dict[str, Any]] = []
    rows: list[dict[str, Any]] = []
    warnings: list[str] = []
    header: dict[str, Any] = {}
    state: dict[str, Any] = {
        "country": None,
        "country_header_page": None,
        "country_group_number": 0,
        "last_row": None,
    }

    with pdfplumber.open(pdf_path) as pdf:
        for page_number, page in enumerate(pdf.pages, start=1):
            text = page.extract_text(x_tolerance=2, y_tolerance=2) or ""
            if page_number == 1:
                header = _extract_header(text)
            words = page.extract_words(keep_blank_chars=False)
            pages.append(
                {
                    "document_id": document_id,
                    "page_number": page_number,
                    "width": float(page.width),
                    "height": float(page.height),
                    "rotation": page.rotation or 0,
                    "raw_text": text,
                    "words": words,
                }
            )
            lines, page_warning = extract_visual_lines(page)
            if page_warning:
                warnings.append(f"page_{page_number}_{page_warning}")
                continue
            parse_visual_lines(
                lines,
                document_id=document_id,
                page_number=page_number,
                state=state,
                rows=rows,
                warnings=warnings,
            )

    if header.get("table_code") not in TABLE_CODES[series]:
        warnings.append("table_code_mismatch")
    if header.get("publication_period") != expected_period:
        warnings.append("period_mismatch")
    if header.get("edition") != edition:
        warnings.append("edition_mismatch")
    countries = [row for row in rows if row["row_type"] == "country_total"]
    commodities = [row for row in rows if row["row_type"] == "commodity"]
    if not countries:
        warnings.append("country_totals_missing")
    if any(row["country_name_raw"] is None for row in commodities):
        warnings.append("orphan_commodities")
    duplicate_countries = len(countries) - len(
        {row["country_name_raw"] for row in countries}
    )
    if duplicate_countries:
        warnings.append("duplicate_country")
    country_checks = _country_value_checks(rows)
    if country_checks["mismatch_count"]:
        warnings.append("country_value_mismatch")

    output_directory.mkdir(parents=True, exist_ok=True)
    document = {
        "document_id": document_id,
        "source_ma": ma,
        "series": series,
        "trade_flow": "export" if series.endswith("X") else "import",
        "report_family": "country_commodity",
        "reference_year": int(expected_period[:4]),
        "reference_month": int(expected_period[5:]),
        "edition": edition,
        "table_code": header.get("table_code"),
        "source_url": source_url,
        "local_path": str(pdf_path),
        "sha256": document_id,
        "size_bytes": pdf_path.stat().st_size,
        "page_count": len(pages),
        "parser_version": PARSER_VERSION,
    }
    validation = {
        "status": "passed" if not warnings else "warning",
        "warnings": warnings,
        "row_count": len(rows),
        "country_count": len(countries),
        "commodity_count": len(commodities),
        "duplicate_country_count": duplicate_countries,
        "country_value_checks": country_checks,
        "expected": {
            "series": series,
            "period": expected_period,
            "edition": edition,
            "table_codes": sorted(TABLE_CODES[series]),
        },
        "extracted_header": header,
    }
    _write_json(output_directory / "document.json", document)
    _write_jsonl(output_directory / "pages.jsonl", pages)
    _write_jsonl(output_directory / "rows.jsonl", rows)
    _write_json(output_directory / "validation.json", validation)
    return ParseResult(
        document_id=document_id,
        rows=len(rows),
        commodities=len(commodities),
        output_directory=output_directory,
        validation_status=validation["status"],
    )


def parse_country_selection(
    records: Iterable[SelectionRecord], *, output_root: Path
) -> tuple[list[ParseResult], list[str]]:
    results: list[ParseResult] = []
    skipped: list[str] = []
    for item in records:
        if item.series not in TABLE_CODES:
            continue
        if not all(
            (item.ma, item.edition, item.publication_period, item.source_url, item.local_path)
        ):
            skipped.append(f"{item.series}:{item.reference_year}:not_selected")
            continue
        pdf_path = Path(item.local_path)
        if not pdf_path.exists():
            skipped.append(f"{item.series}:{item.reference_year}:file_missing")
            continue
        destination = (
            output_root
            / f"parser={PARSER_VERSION}"
            / f"series={item.series}"
            / f"period={item.publication_period}"
            / f"ma={item.ma}"
            / f"edition={item.edition}"
        )
        results.append(
            parse_country_pdf(
                pdf_path,
                series=item.series,
                ma=item.ma,
                edition=item.edition,
                expected_period=item.publication_period,
                source_url=item.source_url,
                output_directory=destination,
            )
        )
    return results, skipped
