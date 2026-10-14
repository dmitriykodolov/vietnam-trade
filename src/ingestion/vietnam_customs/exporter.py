"""Build a flat, analysis-ready CSV layer from parsed annual documents."""

from __future__ import annotations

import csv
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .parser_country import PARSER_VERSION as COUNTRY_PARSER_VERSION
from .parser_summary import PARSER_VERSION as SUMMARY_PARSER_VERSION
from .selector import SelectionRecord

SCHEMA_VERSION = "analytics-v1"

FACT_COLUMNS = (
    "document_id",
    "source_ma",
    "series",
    "trade_flow",
    "report_family",
    "reference_year",
    "reference_month",
    "publication_period",
    "edition",
    "table_code",
    "parser_version",
    "validation_status",
    "coverage_complete",
    "edition_fallback",
    "fallback_reason",
    "fact_level",
    "row_type",
    "country_group_number",
    "country_name_raw",
    "commodity_number",
    "commodity_number_raw",
    "commodity_name_raw",
    "unit_raw",
    "reporting_month_volume",
    "reporting_month_value_usd",
    "mom_volume_pct",
    "mom_value_pct",
    "ytd_volume",
    "ytd_value_usd",
    "yoy_volume_pct",
    "yoy_value_pct",
    "page_number",
    "source_row_number",
    "source_line_top",
    "source_url",
    "local_path",
)

DOCUMENT_COLUMNS = (
    "document_id",
    "source_ma",
    "series",
    "trade_flow",
    "report_family",
    "reference_year",
    "reference_month",
    "publication_period",
    "edition",
    "table_code",
    "parser_version",
    "validation_status",
    "validation_warnings",
    "row_count",
    "commodity_count",
    "country_count",
    "coverage_complete",
    "edition_fallback",
    "fallback_reason",
    "sha256",
    "size_bytes",
    "page_count",
    "source_url",
    "local_path",
)

MEASURE_COLUMNS = (
    "reporting_month_volume",
    "reporting_month_value_usd",
    "mom_volume_pct",
    "mom_value_pct",
    "ytd_volume",
    "ytd_value_usd",
    "yoy_volume_pct",
    "yoy_value_pct",
)


@dataclass(frozen=True)
class ExportResult:
    documents: int
    facts: int
    output_directory: Path
    missing: tuple[str, ...]


def _parser_version(series: str) -> str:
    if series in {"2X", "2N"}:
        return SUMMARY_PARSER_VERSION
    if series in {"5X", "5N"}:
        return COUNTRY_PARSER_VERSION
    raise ValueError(f"unsupported series: {series}")


def _fact_level(report_family: str, row_type: str) -> str:
    if report_family == "country_commodity":
        return "country_total" if row_type == "country_total" else "country_commodity"
    return {
        "total": "national_total",
        "fdi_total": "national_fdi_total",
        "commodity": "national_commodity",
        "other": "national_other",
    }.get(row_type, "national_other")


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _csv_value(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return value


def _write_csv(
    path: Path, columns: tuple[str, ...], rows: list[dict[str, Any]]
) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({column: _csv_value(row.get(column)) for column in columns})
    temporary.replace(path)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _schema() -> dict[str, Any]:
    decimal_columns = list(MEASURE_COLUMNS) + ["source_line_top"]
    integer_columns = [
        "reference_year",
        "reference_month",
        "country_group_number",
        "commodity_number",
        "page_number",
        "source_row_number",
    ]
    return {
        "schema_version": SCHEMA_VERSION,
        "grain": "one published table row from one selected annual document",
        "null_encoding": "empty CSV field",
        "facts": {
            "file": "trade_facts.csv",
            "columns": list(FACT_COLUMNS),
            "decimal_columns": decimal_columns,
            "integer_columns": integer_columns,
            "fact_levels": [
                "national_total",
                "national_fdi_total",
                "national_commodity",
                "national_other",
                "country_total",
                "country_commodity",
            ],
        },
        "documents": {
            "file": "documents.csv",
            "columns": list(DOCUMENT_COLUMNS),
            "primary_key": ["document_id"],
        },
        "notes": [
            "country_name_raw is empty for national 2X/2N rows",
            "mom/yoy percentage fields are empty for 5X/5N rows",
            "names remain source labels; canonical entity mapping is not applied",
            "numeric values use a dot decimal separator and no thousands separator",
        ],
    }


def export_analytics_csv(
    records: list[SelectionRecord], *, extracted_root: Path, output_directory: Path
) -> ExportResult:
    documents: list[dict[str, Any]] = []
    facts: list[dict[str, Any]] = []
    missing: list[str] = []

    for item in sorted(records, key=lambda value: (value.series, value.reference_year)):
        if item.series not in {"2X", "2N", "5X", "5N"}:
            continue
        if not all((item.ma, item.publication_period, item.edition)):
            missing.append(f"{item.series}:{item.reference_year}:not_selected")
            continue
        parser_version = _parser_version(item.series)
        source_directory = (
            extracted_root
            / f"parser={parser_version}"
            / f"series={item.series}"
            / f"period={item.publication_period}"
            / f"ma={item.ma}"
            / f"edition={item.edition}"
        )
        required = [
            source_directory / "document.json",
            source_directory / "rows.jsonl",
            source_directory / "validation.json",
        ]
        if not all(path.exists() for path in required):
            missing.append(f"{item.series}:{item.reference_year}:parsed_output_missing")
            continue

        document = _read_json(required[0])
        rows = _read_jsonl(required[1])
        validation = _read_json(required[2])
        selection_fields = {
            "coverage_complete": item.coverage_complete,
            "edition_fallback": item.edition_fallback,
            "fallback_reason": item.fallback_reason,
        }
        publication_period = f"{int(document['reference_year']):04d}-{int(document['reference_month']):02d}"
        document_row = {
            **document,
            **selection_fields,
            "publication_period": publication_period,
            "validation_status": validation.get("status"),
            "validation_warnings": validation.get("warnings", []),
            "row_count": validation.get("row_count"),
            "commodity_count": validation.get("commodity_count"),
            "country_count": validation.get("country_count"),
        }
        documents.append(document_row)

        common = {
            key: document_row.get(key)
            for key in (
                "document_id",
                "source_ma",
                "series",
                "trade_flow",
                "report_family",
                "reference_year",
                "reference_month",
                "publication_period",
                "edition",
                "table_code",
                "parser_version",
                "validation_status",
                "coverage_complete",
                "edition_fallback",
                "fallback_reason",
                "source_url",
                "local_path",
            )
        }
        for row in rows:
            facts.append(
                {
                    **common,
                    **row,
                    "fact_level": _fact_level(
                        document["report_family"], row.get("row_type", "other")
                    ),
                }
            )

    output_directory.mkdir(parents=True, exist_ok=True)
    facts_path = output_directory / "trade_facts.csv"
    documents_path = output_directory / "documents.csv"
    _write_csv(facts_path, FACT_COLUMNS, facts)
    _write_csv(documents_path, DOCUMENT_COLUMNS, documents)
    schema_path = output_directory / "schema.json"
    schema_path.write_text(
        json.dumps(_schema(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "document_count": len(documents),
        "fact_count": len(facts),
        "validation_passed_count": sum(
            row.get("validation_status") == "passed" for row in documents
        ),
        "missing": missing,
        "files": {
            "trade_facts.csv": {"sha256": _sha256(facts_path), "rows": len(facts)},
            "documents.csv": {
                "sha256": _sha256(documents_path),
                "rows": len(documents),
            },
        },
    }
    (output_directory / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return ExportResult(
        documents=len(documents),
        facts=len(facts),
        output_directory=output_directory,
        missing=tuple(missing),
    )
