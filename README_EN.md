# Vietnam Trade Research

**English** · [Русский](README_RU.md)

Reproducible ingestion of Vietnam Customs scheduled publications: catalogue
discovery, safe document downloads, annual document selection, PDF parsing,
and an analysis-ready dataset for Vietnam–Russia trade research.

Selection, processing, and metric definitions are documented in
[METHODOLOGY_EN.md](METHODOLOGY_EN.md).

## Features

- POST client for `GetTKHQInfo` with timeouts, retries, and exponential backoff;
- pagination through `RowCount`, preserving every original JSON response;
- publication deduplication by `MA`;
- recursive normalization of blank strings and the string value `"null"`;
- a consistent `preliminary`, `revised`, and `final` edition model;
- a complete registry and a focused selection of `2X`, `2N`, `5X`, and `5N`
  publications for `2023-01..2026-09`;
- rejection of internal `10.0.0.0/8` URLs as download candidates;
- annual selection with `final → revised → preliminary` priority;
- dedicated parsers for `2X/2N` summary tables and `5X/5N` country tables;
- raw cell, page text, word-coordinate, provenance, and validation retention;
- a portable processed dataset and a reproducible analytical notebook.

## Publication series

The default document package contains:

- `5X` — Vietnam exports by country and commodity group;
- `5N` — Vietnam imports by country and commodity group;
- `2X` — total Vietnam exports by commodity group;
- `2N` — total Vietnam imports by commodity group.

An optional package enabled with `--include-additional` contains:

- `3X` — exports by foreign-invested enterprises;
- `3N` — imports by foreign-invested enterprises.

Series `4`, `1X`, and `1N` are currently deferred. The standard collection
window covers no more than ten calendar years: `2017-01..2026-09`.

## HTTP request format

The source website uses an unusual request format. The body is the result of
`JSON.stringify(payload)`, meaning raw JSON, while the header remains
`Content-Type: application/x-www-form-urlencoded; charset=UTF-8`. This is not a
regular form-encoded field set. The base payload is:

```json
{
  "skip": 0,
  "take": 20,
  "ky": "",
  "textSearch": "",
  "the_loai": "0",
  "thoigianCongBo": "",
  "typeName": "GetListSoLieu",
  "language": "TIENG_ANH"
}
```

`the_loai` intentionally uses the string value `"0"`; an empty string causes
a server-side numeric conversion error.

## Installation

Python 3.10 or newer is required. PDF extraction uses `pdfplumber`, installed
with the project.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
```

## Ready-to-use analytical dataset

The repository includes the minimum processed dataset required to run the
research notebook without downloading or parsing the source PDFs again:

```text
data/processed/vietnam_customs/
  trade_facts.csv
  documents.csv
  commodity_mapping_approved_2017_2026.csv
  commodity_dictionary_approved.csv
  schema.json
  manifest.json
```

After installing the dependencies, open the notebook from the project root:

```bash
jupyter lab notebooks/russia_vietnam_trade_2017_2026_EN.ipynb
```

The notebook locates the project root automatically and writes reproducible
tables and charts to `outputs/vietnam_trade_2017_2026_extended/`. It contains
no user-specific absolute paths. `data/raw`, `outputs`, and intermediate files
are excluded from Git, so a repository clone is sufficient for analysis.

## Catalogue discovery

Fetch the complete catalogue and build both registries:

```bash
vietnam-customs discover
```

Use a larger page size or custom network settings:

```bash
vietnam-customs discover --take 100 --timeout 30 --max-retries 3
```

Each run preserves the original responses in a timestamped directory:

```text
data/raw/vietnam_customs/metadata/runs/<UTC timestamp>/
```

The normalized registries are written to:

```text
data/raw/vietnam_customs/metadata/publications_registry.json
data/raw/vietnam_customs/metadata/series_2X_2N_5X_5N_2023-01_2026-09.json
```

Rebuild registries from an existing raw run without accessing the website:

```bash
vietnam-customs registry \
  --raw-dir data/raw/vietnam_customs/metadata/runs/<UTC timestamp>
```

Run without installing the console entry point:

```bash
PYTHONPATH=src python -m ingestion.vietnam_customs discover
```

## Tests

```bash
PYTHONPATH=src python -m unittest discover -s tests -v
```

The tests do not access the network or download PDFs.

## Document downloads

Inspect the candidate set without network requests:

```bash
vietnam-customs download --dry-run
```

Download the default `5X/5N/2X/2N` package for `2017-01..2026-09`:

```bash
vietnam-customs download --workers 4
```

Include the `3X/3N` series:

```bash
vietnam-customs download --workers 4 --include-additional
```

Files are stored as
`documents/<series>/<YYYY-MM>/<MA>/<edition>-<source filename>`. A repeated run
skips existing files that pass signature validation. Every outcome is appended
immediately to `documents/download_manifest.jsonl`, including its SHA-256,
size, errors, and rejected URLs.

Only the exact host `files.customs.gov.vn` is allowed. Internal, concatenated,
malformed, and unsupported URLs are never requested. Redirects, response size,
and file signatures are validated as well.

## Annual selection and PDF parsing

Select one document per series and reporting year for `2017-01..2026-09`:

```bash
vietnam-customs select
```

For completed years, the selector prefers December. For 2026, it uses the
latest available month. Within a period, the priority is
`final → revised → preliminary`. A missing December document or edition
fallback is recorded explicitly in:

```text
data/raw/vietnam_customs/metadata/document_selection.jsonl
```

Review and download only the 40 selected annual documents:

```bash
vietnam-customs download-selected --dry-run
vietnam-customs download-selected --workers 4
```

Parse the selected `2X/2N` documents:

```bash
vietnam-customs parse-summary
```

Parse the selected `5X/5N` country documents, carrying the active country
across page boundaries:

```bash
vietnam-customs parse-country
```

Parse all four series and export the CSV layer in one command:

```bash
vietnam-customs build-dataset
```

If the PDFs have already been parsed, rebuild only the analytical CSV files:

```bash
vietnam-customs export-csv
```

The resulting analytical layer is:

```text
data/processed/vietnam_customs/
  trade_facts.csv  # all published rows from 2X/2N/5X/5N
  documents.csv    # provenance and validation for 40 selected documents
  schema.json      # machine-readable columns and types
  manifest.json    # row counts, missing inputs, and file checksums
```

In `trade_facts.csv`, `trade_flow` identifies exports or imports, while
`reference_year`, `reference_month`, and `publication_period` identify the
reporting period. `2X/2N` rows have no country and use a `national_*`
`fact_level`. `5X/5N` rows include `country_name_raw` and use
`country_total` or `country_commodity`.

For every parsed PDF, the raw extraction layer contains:

```text
data/raw/vietnam_customs/extracted/
  parser=summary-v1/series=<series>/period=<YYYY-MM>/ma=<MA>/edition=<edition>/
    document.json
    pages.jsonl
    rows.jsonl
    validation.json
```

`5X/5N` use the equivalent structure under `parser=country-v1`.

Numeric values are written to JSON as canonical decimal strings to avoid loss
of precision. Original rows, cells, page text, and word coordinates are stored
alongside the normalized values. The processed analytical schema is available
in `data/processed/vietnam_customs/schema.json`.
