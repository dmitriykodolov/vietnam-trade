"""Parser for Vietnam Customs 2X/2N monthly summary PDF tables."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable

import pdfplumber

from .selector import SelectionRecord


PARSER_VERSION = "summary-v1"
TABLE_CODES = {
    "2X": frozenset({"14B/TCHQ", "2X/TCHQ"}),
    "2N": frozenset({"15B/TCHQ", "2N/TCHQ"}),
}
TABLE_SETTINGS = {
    "vertical_strategy": "lines",
    "horizontal_strategy": "lines",
    "snap_tolerance": 3,
    "join_tolerance": 3,
    "intersection_tolerance": 5,
}
MONTHS = {
    name: number
    for number, name in enumerate(
        (
            "January",
            "February",
            "March",
            "April",
            "May",
            "June",
            "July",
            "August",
            "September",
            "October",
            "November",
            "December",
        ),
        start=1,
    )
}


def _clean(value: Any) -> str | None:
    if value is None:
        return None
    text = re.sub(r"\s+", " ", str(value)).strip()
    return text or None


def parse_number(value: Any) -> str | None:
    """Return a canonical decimal string while preserving exact precision."""
    text = _clean(value)
    if text is None:
        return None
    compact = text.replace(",", "")
    if not re.fullmatch(r"-?\d+(?:\.\d+)?", compact):
        raise ValueError(f"not a number: {text!r}")
    return compact


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _extract_header(text: str) -> dict[str, Any]:
    table = re.search(r"Table:\s*([0-9A-Z/]+)", text, re.IGNORECASE)
    period = re.search(
        r"\b(" + "|".join(MONTHS) + r"),\s*(20\d{2})\b", text
    )
    edition = next(
        (value for value in ("final", "revised", "preliminary") if re.search(rf"\b{value}\b", text, re.IGNORECASE)),
        None,
    )
    return {
        "table_code": table.group(1).upper() if table else None,
        "publication_period": (
            f"{int(period.group(2)):04d}-{MONTHS[period.group(1)]:02d}"
            if period
            else None
        ),
        "edition": edition,
    }


def parse_table_row(
    cells: list[Any], *, document_id: str, page_number: int, source_row_number: int
) -> dict[str, Any]:
    if len(cells) != 11:
        raise ValueError(f"expected 11 cells, got {len(cells)}")
    raw = [_clean(value) for value in cells]
    number, commodity, unit = raw[:3]
    if number and number.isdigit():
        row_type = "commodity"
    elif commodity and commodity.upper() == "TOTAL VALUE":
        row_type = "total"
    elif commodity and commodity.lower().startswith("in which:"):
        row_type = "fdi_total"
    else:
        row_type = "other"

    numeric_names = (
        "reporting_month_volume",
        "reporting_month_value_usd",
        "mom_volume_pct",
        "mom_value_pct",
        "ytd_volume",
        "ytd_value_usd",
        "yoy_volume_pct",
        "yoy_value_pct",
    )
    parsed: dict[str, Any] = {
        "document_id": document_id,
        "page_number": page_number,
        "source_row_number": source_row_number,
        "row_type": row_type,
        "commodity_number_raw": number,
        "commodity_number": int(number) if number and number.isdigit() else None,
        "commodity_name_raw": commodity,
        "unit_raw": unit,
        "raw_cells": raw,
        "parse_warnings": [],
    }
    for offset, name in enumerate(numeric_names, start=3):
        parsed[f"{name}_raw"] = raw[offset]
        try:
            parsed[name] = parse_number(raw[offset])
        except ValueError:
            parsed[name] = None
            parsed["parse_warnings"].append(f"invalid_{name}")
    return parsed


def _normalise_cells(cells: list[Any]) -> list[Any]:
    """Remove the empty leading column present in the 2018-2021 layout."""
    if len(cells) == 12 and _clean(cells[0]) is None:
        return cells[1:]
    return cells


@dataclass(frozen=True)
class ParseResult:
    document_id: str
    rows: int
    commodities: int
    output_directory: Path
    validation_status: str


def _write_json(path: Path, value: Any) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, values: Iterable[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as stream:
        for value in values:
            stream.write(json.dumps(value, ensure_ascii=False) + "\n")


def parse_summary_pdf(
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
        raise ValueError("summary parser supports only 2X and 2N")
    document_id = _sha256(pdf_path)
    pages: list[dict[str, Any]] = []
    rows: list[dict[str, Any]] = []
    warnings: list[str] = []
    header: dict[str, Any] = {}
    source_row_number = 0

    with pdfplumber.open(pdf_path) as pdf:
        for page_number, page in enumerate(pdf.pages, start=1):
            text = page.extract_text(x_tolerance=2, y_tolerance=2) or ""
            if page_number == 1:
                header = _extract_header(text)
            pages.append(
                {
                    "document_id": document_id,
                    "page_number": page_number,
                    "width": float(page.width),
                    "height": float(page.height),
                    "rotation": page.rotation or 0,
                    "raw_text": text,
                    "words": page.extract_words(keep_blank_chars=False),
                }
            )
            tables = page.extract_tables(TABLE_SETTINGS)
            if len(tables) != 1:
                warnings.append(f"page_{page_number}_table_count_{len(tables)}")
                continue
            table = tables[0]
            if len(table) < 2 or len(_normalise_cells(table[0])) != 11:
                warnings.append(f"page_{page_number}_invalid_header")
                continue
            for cells in table[2:]:
                if (
                    len(cells) == 12
                    and _clean(cells[0])
                    and not any(_clean(value) for value in cells[1:])
                ):
                    # Old PDFs contain a novaPDF promotional footer captured
                    # as a one-cell table row. It is preserved in pages.jsonl.
                    continue
                cells = _normalise_cells(cells)
                if not any(_clean(value) for value in cells):
                    continue
                source_row_number += 1
                try:
                    row = parse_table_row(
                        cells,
                        document_id=document_id,
                        page_number=page_number,
                        source_row_number=source_row_number,
                    )
                    rows.append(row)
                except ValueError as exc:
                    warnings.append(f"page_{page_number}_row_{source_row_number}:{exc}")

    if header.get("table_code") not in TABLE_CODES[series]:
        warnings.append("table_code_mismatch")
    if header.get("publication_period") != expected_period:
        warnings.append("period_mismatch")
    if header.get("edition") != edition:
        warnings.append("edition_mismatch")

    commodity_numbers = [
        row["commodity_number"]
        for row in rows
        if row["row_type"] == "commodity"
    ]
    if len(commodity_numbers) != len(set(commodity_numbers)):
        warnings.append("duplicate_commodity_number")
    if commodity_numbers and sorted(commodity_numbers) != list(
        range(min(commodity_numbers), max(commodity_numbers) + 1)
    ):
        warnings.append("commodity_number_gap")
    if not any(row["row_type"] == "total" for row in rows):
        warnings.append("total_row_missing")

    output_directory.mkdir(parents=True, exist_ok=True)
    document = {
        "document_id": document_id,
        "source_ma": ma,
        "series": series,
        "trade_flow": "export" if series.endswith("X") else "import",
        "report_family": "commodity_summary",
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
        "commodity_count": len(commodity_numbers),
        "row_type_counts": {
            kind: sum(row["row_type"] == kind for row in rows)
            for kind in ("total", "fdi_total", "commodity", "other")
        },
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
        commodities=len(commodity_numbers),
        output_directory=output_directory,
        validation_status=validation["status"],
    )


def parse_selection(
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
            parse_summary_pdf(
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
