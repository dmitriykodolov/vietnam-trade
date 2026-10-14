from __future__ import annotations

import csv
import json
import tempfile
import unittest
from pathlib import Path

from ingestion.vietnam_customs.client import VietnamCustomsClient
from ingestion.vietnam_customs.discovery import discover_publications
from ingestion.vietnam_customs.downloader import (
    collect_candidates,
    validate_download_url,
)
from ingestion.vietnam_customs.exporter import export_analytics_csv
from ingestion.vietnam_customs.parser_country import (
    _country_value_checks,
    parse_visual_lines,
)
from ingestion.vietnam_customs.parser_summary import (
    _normalise_cells,
    parse_number,
    parse_table_row,
)
from ingestion.vietnam_customs.registry import (
    build_registry,
    filter_target_series,
    is_internal_10_url,
    normalize_nulls,
    parse_period,
)
from ingestion.vietnam_customs.selector import SelectionRecord, select_annual_documents


class FakeResponse:
    def __init__(self, payload: dict):
        self.payload = json.dumps(payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def read(self):
        return self.payload


class CapturingOpener:
    def __init__(self, payload: dict):
        self.payload = payload
        self.request = None
        self.timeout = None

    def open(self, request, timeout):
        self.request = request
        self.timeout = timeout
        return FakeResponse(self.payload)


class ClientTests(unittest.TestCase):
    def test_posts_raw_json_with_browser_content_type(self):
        opener = CapturingOpener({"arr": [], "RowCount": 0})
        client = VietnamCustomsClient(opener=opener, timeout=7)

        client.fetch_page(skip=0, take=20)

        body = json.loads(opener.request.data.decode())
        self.assertEqual(body["the_loai"], "0")
        self.assertEqual(body["typeName"], "GetListSoLieu")
        self.assertEqual(body["language"], "TIENG_ANH")
        self.assertEqual(
            opener.request.get_header("Content-type"),
            "application/x-www-form-urlencoded; charset=UTF-8",
        )
        self.assertEqual(opener.timeout, 7)


class FakeClient:
    def __init__(self):
        self.skips = []

    def fetch_page(self, *, skip, take, **_filters):
        self.skips.append(skip)
        pages = {
            0: {"arr": [{"MA": "1"}, {"MA": "2"}], "RowCount": 3},
            2: {"arr": [{"MA": "2"}, {"MA": "3"}], "RowCount": 3},
        }
        return pages[skip]


class DiscoveryTests(unittest.TestCase):
    def test_pages_to_row_count_and_deduplicates_by_ma(self):
        with tempfile.TemporaryDirectory() as directory:
            result = discover_publications(
                FakeClient(), raw_directory=Path(directory), take=2
            )
            self.assertEqual(
                [item["MA"] for item in result.publications], ["1", "2", "3"]
            )
            self.assertEqual(result.duplicates_removed, 1)
            self.assertEqual(result.pages_fetched, 2)
            self.assertTrue((Path(directory) / "page_000000.json").exists())
            self.assertTrue((Path(directory) / "manifest.json").exists())


class RegistryTests(unittest.TestCase):
    def test_normalizes_nulls_and_blocks_internal_url(self):
        source = {
            "MA": "8658",
            "TIEU_DE": "Example",
            "NGAY_CONG_BO": "09-2026",
            "TOM_TAT": "null",
            "FILE_SO_BO": "https://files.customs.gov.vn/x/2026-T9-2X(EN).pdf",
            "NGAY_SO_BO": "09/10/2026",
            "FILE_DIEU_CHINH": " null ",
            "NGAY_DIEU_CHINH": "null",
            "FILE_CHINH_THUC": "http://10.224.128.185/x/2026-T9-2X.pdf",
            "NGAY_CHINH_THUC": "23/04/2027",
        }

        item = build_registry([source])[0]

        self.assertIsNone(item["source"]["TOM_TAT"])
        self.assertEqual(item["publication_period"], "2026-09")
        self.assertEqual(item["series"], ["2X"])
        self.assertIsNone(item["editions"]["final"]["download_url"])
        self.assertTrue(item["editions"]["final"]["blocked_internal_url"])
        self.assertEqual(filter_target_series([item]), [item])

    def test_internal_10_url_detection_is_host_based(self):
        self.assertTrue(is_internal_10_url("http://10.1.2.3/file.pdf"))
        self.assertFalse(
            is_internal_10_url("https://example.com/path/10.1.2.3/file.pdf")
        )
        self.assertFalse(is_internal_10_url("https://files.customs.gov.vn/file.pdf"))

    def test_recursive_null_normalization(self):
        self.assertEqual(
            normalize_nulls({"a": "null", "b": [" NULL ", "value"]}),
            {"a": None, "b": [None, "value"]},
        )

    def test_rejects_implausible_period(self):
        self.assertIsNone(parse_period("01-0011"))


class DownloaderTests(unittest.TestCase):
    def test_url_allowlist_rejects_internal_and_malformed_urls(self):
        self.assertEqual(
            validate_download_url("http://10.1.2.3/file.pdf"),
            (False, "internal_10_url"),
        )
        self.assertEqual(
            validate_download_url(
                "https://files.customs.gov.vnhttp://10.1.2.3/file.pdf"
            ),
            (False, "host_not_allowed"),
        )
        self.assertEqual(
            validate_download_url(
                "https://files.customs.gov.vn/http:///TONG_CUC/file.pdf"
            ),
            (False, "nested_or_malformed_url"),
        )
        self.assertEqual(
            validate_download_url("https://files.customs.gov.vn/x/file.pdf"),
            (True, None),
        )

    def test_candidate_priorities_and_ten_year_window(self):
        publications = []
        for series in ("2X", "3X", "1X"):
            publications.append(
                {
                    "ma": series,
                    "publication_period": "2017-01",
                    "editions": {
                        "preliminary": {
                            "series": series,
                            "source_url": f"https://files.customs.gov.vn/{series}.pdf",
                        }
                    },
                }
            )
        publications.append(
            {
                "ma": "old",
                "publication_period": "2016-12",
                "editions": {
                    "preliminary": {
                        "series": "2X",
                        "source_url": "https://files.customs.gov.vn/old.pdf",
                    }
                },
            }
        )
        core, rejected = collect_candidates(
            publications, output_directory=Path("documents")
        )
        expanded, _ = collect_candidates(
            publications,
            output_directory=Path("documents"),
            include_additional=True,
        )
        self.assertEqual([item.series for item in core], ["2X"])
        self.assertEqual([item.series for item in expanded], ["2X", "3X"])
        self.assertEqual(rejected, [])


class SelectorTests(unittest.TestCase):
    @staticmethod
    def publication(ma, period, editions):
        return {
            "ma": ma,
            "publication_period": period,
            "editions": {
                name: {
                    "series": "2X",
                    "source_url": f"https://files.customs.gov.vn/{ma}-{name}-2X.pdf",
                }
                for name in editions
            },
        }

    def test_closed_year_prefers_december_final_and_current_uses_latest(self):
        publications = [
            self.publication("1", "2025-11", ["final"]),
            self.publication("2", "2025-12", ["preliminary", "final"]),
            self.publication("3", "2026-08", ["final"]),
            self.publication("4", "2026-09", ["preliminary"]),
        ]
        records = select_annual_documents(
            publications,
            documents_directory=Path("documents"),
            start="2025-01",
            end="2026-09",
            series=["2X"],
        )
        self.assertEqual(records[0].publication_period, "2025-12")
        self.assertEqual(records[0].edition, "final")
        self.assertTrue(records[0].coverage_complete)
        self.assertEqual(records[1].publication_period, "2026-09")
        self.assertEqual(records[1].edition, "preliminary")
        self.assertFalse(records[1].coverage_complete)
        self.assertIn("final_unavailable", records[1].fallback_reason)


class SummaryParserTests(unittest.TestCase):
    def test_number_parser_preserves_decimal_precision(self):
        self.assertEqual(parse_number("44,077,885,178"), "44077885178")
        self.assertEqual(parse_number("-10.8"), "-10.8")
        self.assertIsNone(parse_number(""))

    def test_parses_eleven_cell_summary_row(self):
        row = parse_table_row(
            [
                "3",
                "Cashew nut",
                "Ton",
                "68,030",
                "465,329,471",
                "-1.0",
                "-1.9",
                "766,525",
                "5,228,868,159",
                "5.7",
                "20.4",
            ],
            document_id="abc",
            page_number=1,
            source_row_number=5,
        )
        self.assertEqual(row["row_type"], "commodity")
        self.assertEqual(row["commodity_number"], 3)
        self.assertEqual(row["reporting_month_volume"], "68030")
        self.assertEqual(row["ytd_value_usd"], "5228868159")
        self.assertEqual(row["parse_warnings"], [])

    def test_normalizes_historical_leading_empty_column(self):
        cells = [
            None,
            "1",
            "Fishery products",
            "USD",
            "",
            "1",
            "",
            "2",
            "",
            "3",
            "",
            "4",
        ]
        self.assertEqual(len(_normalise_cells(cells)), 11)
        self.assertEqual(_normalise_cells(cells)[0], "1")


class CountryParserTests(unittest.TestCase):
    def test_carries_country_across_pages_and_joins_wrapped_names(self):
        rows = []
        warnings = []
        state = {
            "country": None,
            "country_header_page": None,
            "country_group_number": 0,
            "last_row": None,
        }
        parse_visual_lines(
            [
                {
                    "top": 100.0,
                    "values": ["Argentina", None, None, "100", None, "900"],
                    "name_is_bold": True,
                },
                {
                    "top": 115.0,
                    "values": [
                        "Textile, leather and foot-wear",
                        "USD",
                        None,
                        "40",
                        None,
                        "300",
                    ],
                    "name_is_bold": False,
                },
                {
                    "top": 128.0,
                    "values": [
                        "materials and auxiliaries",
                        None,
                        None,
                        None,
                        None,
                        None,
                    ],
                    "name_is_bold": False,
                },
            ],
            document_id="doc",
            page_number=1,
            state=state,
            rows=rows,
            warnings=warnings,
        )
        parse_visual_lines(
            [
                {
                    "top": 100.0,
                    "values": ["Other products", "USD", None, "60", None, "600"],
                    "name_is_bold": False,
                }
            ],
            document_id="doc",
            page_number=2,
            state=state,
            rows=rows,
            warnings=warnings,
        )
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[1]["country_name_raw"], "Argentina")
        self.assertEqual(
            rows[1]["commodity_name_raw"],
            "Textile, leather and foot-wear materials and auxiliaries",
        )
        self.assertTrue(rows[2]["country_inherited"])
        self.assertEqual(warnings, [])

    def test_reconciliation_requires_other_products_and_allows_source_rounding(self):
        legacy = [
            {
                "country_group_number": 1,
                "row_type": "country_total",
                "country_name_raw": "A",
                "reporting_month_value_usd": "100",
                "ytd_value_usd": "1000",
            },
            {
                "country_group_number": 1,
                "row_type": "commodity",
                "commodity_name_raw": "Selected product",
                "reporting_month_value_usd": "40",
                "ytd_value_usd": "400",
            },
        ]
        self.assertEqual(_country_value_checks(legacy)["checked"], 0)

        balanced = legacy + [
            {
                "country_group_number": 1,
                "row_type": "commodity",
                "commodity_name_raw": "Other products",
                "reporting_month_value_usd": "56",
                "ytd_value_usd": "595",
            }
        ]
        result = _country_value_checks(balanced)
        self.assertEqual(result["checked"], 2)
        self.assertEqual(result["mismatch_count"], 0)


class ExporterTests(unittest.TestCase):
    def test_combines_trade_flow_period_and_grains(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            extracted = root / "extracted"
            output = root / "processed"
            records = []
            fixtures = [
                ("2X", "summary-v1", "export", "commodity_summary", "commodity"),
                ("5N", "country-v1", "import", "country_commodity", "commodity"),
            ]
            for index, (series, parser, flow, family, row_type) in enumerate(
                fixtures, start=1
            ):
                ma = str(index)
                destination = (
                    extracted
                    / f"parser={parser}"
                    / f"series={series}"
                    / "period=2025-12"
                    / f"ma={ma}"
                    / "edition=final"
                )
                destination.mkdir(parents=True)
                document = {
                    "document_id": f"doc-{index}",
                    "source_ma": ma,
                    "series": series,
                    "trade_flow": flow,
                    "report_family": family,
                    "reference_year": 2025,
                    "reference_month": 12,
                    "edition": "final",
                    "table_code": "test",
                    "source_url": "https://files.customs.gov.vn/test.pdf",
                    "local_path": "/tmp/test.pdf",
                    "sha256": f"doc-{index}",
                    "size_bytes": 1,
                    "page_count": 1,
                    "parser_version": parser,
                }
                row = {
                    "document_id": f"doc-{index}",
                    "page_number": 1,
                    "source_row_number": 1,
                    "row_type": row_type,
                    "commodity_name_raw": "Coffee",
                    "unit_raw": "Ton",
                    "reporting_month_volume": "10",
                    "reporting_month_value_usd": "20",
                    "ytd_volume": "100",
                    "ytd_value_usd": "200",
                }
                if series == "5N":
                    row["country_group_number"] = 1
                    row["country_name_raw"] = "Japan"
                (destination / "document.json").write_text(json.dumps(document))
                (destination / "rows.jsonl").write_text(json.dumps(row) + "\n")
                (destination / "validation.json").write_text(
                    json.dumps(
                        {
                            "status": "passed",
                            "warnings": [],
                            "row_count": 1,
                            "commodity_count": 1,
                            "country_count": 1 if series == "5N" else None,
                        }
                    )
                )
                records.append(
                    SelectionRecord(
                        series=series,
                        reference_year=2025,
                        publication_period="2025-12",
                        ma=ma,
                        edition="final",
                        source_url=document["source_url"],
                        local_path=document["local_path"],
                        file_exists=True,
                        is_complete_year=True,
                        coverage_complete=True,
                        edition_fallback=False,
                        fallback_reason=None,
                    )
                )

            result = export_analytics_csv(
                records, extracted_root=extracted, output_directory=output
            )
            with (output / "trade_facts.csv").open(newline="") as stream:
                rows = list(csv.DictReader(stream))

            self.assertEqual(result.documents, 2)
            self.assertEqual(result.facts, 2)
            self.assertEqual({row["trade_flow"] for row in rows}, {"export", "import"})
            self.assertEqual({row["publication_period"] for row in rows}, {"2025-12"})
            self.assertEqual(
                {row["fact_level"] for row in rows},
                {"national_commodity", "country_commodity"},
            )


if __name__ == "__main__":
    unittest.main()
