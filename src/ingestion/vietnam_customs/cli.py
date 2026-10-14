"""Command-line entry point for catalogue discovery and registry generation."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path

from .client import VietnamCustomsClient
from .discovery import discover_publications, load_raw_pages
from .downloader import DocumentDownloader, collect_candidates, download_candidates
from .exporter import export_analytics_csv
from .parser_country import parse_country_selection
from .parser_summary import parse_selection
from .registry import build_registry, filter_target_series, write_registry
from .selector import (
    read_selection,
    select_annual_documents,
    selection_download_candidates,
    write_selection,
)

DEFAULT_METADATA_ROOT = Path("data/raw/vietnam_customs/metadata")
DEFAULT_DOCUMENTS_ROOT = Path("data/raw/vietnam_customs/documents")
DEFAULT_SELECTION = DEFAULT_METADATA_ROOT / "document_selection.jsonl"
DEFAULT_EXTRACTED_ROOT = Path("data/raw/vietnam_customs/extracted")
DEFAULT_ANALYTICS_ROOT = Path("data/processed/vietnam_customs")


def _registry_outputs(records: list[dict], output_directory: Path) -> tuple[Path, Path]:
    registry = build_registry(records)
    target = filter_target_series(registry)
    all_path = output_directory / "publications_registry.json"
    target_path = output_directory / "series_2X_2N_5X_5N_2023-01_2026-09.json"
    write_registry(all_path, registry)
    write_registry(target_path, target)
    return all_path, target_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="vietnam-customs")
    subparsers = parser.add_subparsers(dest="command", required=True)

    discover = subparsers.add_parser(
        "discover", help="fetch all catalogue pages and build registries"
    )
    discover.add_argument("--output-root", type=Path, default=DEFAULT_METADATA_ROOT)
    discover.add_argument("--take", type=int, default=20)
    discover.add_argument("--timeout", type=float, default=30.0)
    discover.add_argument("--max-retries", type=int, default=3)

    registry = subparsers.add_parser(
        "registry", help="rebuild registries from an existing raw run"
    )
    registry.add_argument("--raw-dir", type=Path, required=True)
    registry.add_argument("--output-dir", type=Path)

    download = subparsers.add_parser(
        "download", help="download selected publication series safely"
    )
    download.add_argument(
        "--registry",
        type=Path,
        default=DEFAULT_METADATA_ROOT / "publications_registry.json",
    )
    download.add_argument(
        "--output-dir", type=Path, default=Path("data/raw/vietnam_customs/documents")
    )
    download.add_argument("--include-additional", action="store_true")
    download.add_argument("--start", default="2017-01")
    download.add_argument("--end", default="2026-09")
    download.add_argument("--workers", type=int, default=4)
    download.add_argument("--timeout", type=float, default=60.0)
    download.add_argument("--max-retries", type=int, default=3)
    download.add_argument("--max-mb", type=int, default=100)
    download.add_argument("--dry-run", action="store_true")

    select = subparsers.add_parser(
        "select", help="select one annual document per priority series"
    )
    select.add_argument(
        "--registry",
        type=Path,
        default=DEFAULT_METADATA_ROOT / "publications_registry.json",
    )
    select.add_argument("--documents-dir", type=Path, default=DEFAULT_DOCUMENTS_ROOT)
    select.add_argument("--output", type=Path, default=DEFAULT_SELECTION)
    select.add_argument("--start", default="2017-01")
    select.add_argument("--end", default="2026-09")

    selected_download = subparsers.add_parser(
        "download-selected", help="download only documents in the annual selection"
    )
    selected_download.add_argument("--selection", type=Path, default=DEFAULT_SELECTION)
    selected_download.add_argument(
        "--manifest",
        type=Path,
        default=DEFAULT_DOCUMENTS_ROOT / "selected_download_manifest.jsonl",
    )
    selected_download.add_argument("--workers", type=int, default=4)
    selected_download.add_argument("--timeout", type=float, default=60.0)
    selected_download.add_argument("--max-retries", type=int, default=3)
    selected_download.add_argument("--max-mb", type=int, default=100)
    selected_download.add_argument("--dry-run", action="store_true")

    parse = subparsers.add_parser(
        "parse-summary", help="parse selected 2X/2N PDF tables"
    )
    parse.add_argument("--selection", type=Path, default=DEFAULT_SELECTION)
    parse.add_argument("--output-root", type=Path, default=DEFAULT_EXTRACTED_ROOT)

    parse_country = subparsers.add_parser(
        "parse-country", help="parse selected 5X/5N country-product PDF tables"
    )
    parse_country.add_argument("--selection", type=Path, default=DEFAULT_SELECTION)
    parse_country.add_argument(
        "--output-root", type=Path, default=DEFAULT_EXTRACTED_ROOT
    )

    export_csv = subparsers.add_parser(
        "export-csv", help="combine parsed 2X/2N/5X/5N rows into analytics CSVs"
    )
    export_csv.add_argument("--selection", type=Path, default=DEFAULT_SELECTION)
    export_csv.add_argument(
        "--extracted-root", type=Path, default=DEFAULT_EXTRACTED_ROOT
    )
    export_csv.add_argument("--output-dir", type=Path, default=DEFAULT_ANALYTICS_ROOT)

    build_dataset = subparsers.add_parser(
        "build-dataset", help="parse all four selected series and export CSVs"
    )
    build_dataset.add_argument("--selection", type=Path, default=DEFAULT_SELECTION)
    build_dataset.add_argument(
        "--extracted-root", type=Path, default=DEFAULT_EXTRACTED_ROOT
    )
    build_dataset.add_argument(
        "--output-dir", type=Path, default=DEFAULT_ANALYTICS_ROOT
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "discover":
        run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        run_directory = args.output_root / "runs" / run_id
        client = VietnamCustomsClient(
            timeout=args.timeout, max_retries=args.max_retries
        )
        result = discover_publications(
            client, raw_directory=run_directory, take=args.take
        )
        all_path, target_path = _registry_outputs(result.publications, args.output_root)
        print(
            f"Fetched {result.pages_fetched} pages; "
            f"{len(result.publications)} unique publications "
            f"({result.duplicates_removed} duplicates removed)."
        )
        print(f"Raw responses: {run_directory}")
        print(f"All publications: {all_path}")
        print(f"Target series: {target_path}")
        return 0

    if args.command == "registry":
        records = load_raw_pages(args.raw_dir)
        output_directory = args.output_dir or args.raw_dir.parent.parent
        all_path, target_path = _registry_outputs(records, output_directory)
        print(f"Built registry from {len(records)} unique publications.")
        print(f"All publications: {all_path}")
        print(f"Target series: {target_path}")
        return 0

    if args.command == "select":
        payload = json.loads(args.registry.read_text(encoding="utf-8"))
        records = select_annual_documents(
            payload["publications"],
            documents_directory=args.documents_dir,
            start=args.start,
            end=args.end,
        )
        write_selection(args.output, records)
        missing = sum(record.ma is None for record in records)
        fallbacks = sum(record.fallback_reason is not None for record in records)
        print(
            f"Selected {len(records) - missing}/{len(records)} annual documents; "
            f"fallbacks={fallbacks}, missing={missing}."
        )
        print(f"Selection: {args.output}")
        return 0

    if args.command == "download-selected":
        candidates = selection_download_candidates(read_selection(args.selection))
        print(f"Selected annual downloads: {len(candidates)}")
        if args.dry_run:
            return 0
        results = download_candidates(
            candidates,
            downloader=DocumentDownloader(
                timeout=args.timeout,
                max_retries=args.max_retries,
                max_bytes=args.max_mb * 1024 * 1024,
            ),
            manifest_path=args.manifest,
            workers=args.workers,
        )
        counts: dict[str, int] = {}
        for result in results:
            counts[result.status] = counts.get(result.status, 0) + 1
        print(
            "Results: "
            + ", ".join(f"{key}={value}" for key, value in sorted(counts.items()))
        )
        print(f"Manifest: {args.manifest}")
        return 0 if not counts.get("failed") else 1

    if args.command == "parse-summary":
        results, skipped = parse_selection(
            read_selection(args.selection), output_root=args.output_root
        )
        warnings = sum(result.validation_status != "passed" for result in results)
        print(
            f"Parsed {len(results)} summary PDFs; warnings={warnings}, "
            f"skipped={len(skipped)}."
        )
        if skipped:
            print("Skipped: " + ", ".join(skipped))
        return 0 if not warnings else 1

    if args.command == "parse-country":
        results, skipped = parse_country_selection(
            read_selection(args.selection), output_root=args.output_root
        )
        warnings = sum(result.validation_status != "passed" for result in results)
        print(
            f"Parsed {len(results)} country PDFs; warnings={warnings}, "
            f"skipped={len(skipped)}."
        )
        if skipped:
            print("Skipped: " + ", ".join(skipped))
        return 0 if not warnings else 1

    if args.command == "export-csv":
        result = export_analytics_csv(
            read_selection(args.selection),
            extracted_root=args.extracted_root,
            output_directory=args.output_dir,
        )
        print(
            f"Exported {result.facts} facts from {result.documents} documents; "
            f"missing={len(result.missing)}."
        )
        print(f"Dataset: {result.output_directory}")
        return 0 if not result.missing else 1

    if args.command == "build-dataset":
        records = read_selection(args.selection)
        summary_results, summary_skipped = parse_selection(
            records, output_root=args.extracted_root
        )
        country_results, country_skipped = parse_country_selection(
            records, output_root=args.extracted_root
        )
        results = summary_results + country_results
        skipped = summary_skipped + country_skipped
        warnings = sum(result.validation_status != "passed" for result in results)
        exported = export_analytics_csv(
            records,
            extracted_root=args.extracted_root,
            output_directory=args.output_dir,
        )
        print(
            f"Parsed {len(results)} PDFs; warnings={warnings}, skipped={len(skipped)}."
        )
        print(
            f"Exported {exported.facts} facts from {exported.documents} documents; "
            f"missing={len(exported.missing)}."
        )
        print(f"Dataset: {exported.output_directory}")
        return 0 if not (warnings or skipped or exported.missing) else 1

    payload = json.loads(args.registry.read_text(encoding="utf-8"))
    candidates, rejected = collect_candidates(
        payload["publications"],
        output_directory=args.output_dir,
        include_additional=args.include_additional,
        start=args.start,
        end=args.end,
    )
    selection = "2X,2N,5X,5N"
    if args.include_additional:
        selection += ",3X,3N"
    print(
        f"Selected series {selection}: {len(candidates)} downloadable; "
        f"{len(rejected)} rejected URLs."
    )
    if args.dry_run:
        return 0
    manifest_path = args.output_dir / "download_manifest.jsonl"
    results = download_candidates(
        candidates,
        downloader=DocumentDownloader(
            timeout=args.timeout,
            max_retries=args.max_retries,
            max_bytes=args.max_mb * 1024 * 1024,
        ),
        manifest_path=manifest_path,
        workers=args.workers,
        initial_results=rejected,
    )
    counts: dict[str, int] = {}
    for result in results:
        counts[result.status] = counts.get(result.status, 0) + 1
    print(
        "Results: "
        + ", ".join(f"{key}={value}" for key, value in sorted(counts.items()))
    )
    print(f"Manifest: {manifest_path}")
    return 0 if not counts.get("failed") else 1
