# Curated Vietnam Customs dataset

This directory is the portable data layer committed with the repository. It is
enough to run either localized notebook without the raw Vietnam Customs
downloads:

- `notebooks/russia_vietnam_trade_2017_2026_EN.ipynb`;
- `notebooks/russia_vietnam_trade_2017_2026_RU.ipynb`.

| File | Purpose |
| --- | --- |
| `trade_facts.csv` | 25,108 normalized national and country-level trade facts |
| `documents.csv` | Provenance and validation metadata for 40 selected publications |
| `commodity_mapping_approved_2017_2026.csv` | Source label to canonical commodity mapping |
| `commodity_dictionary_approved.csv` | Canonical L2 commodity and L1 analytical group dictionary |
| `schema.json` | Column, type, grain, and null-encoding description |
| `manifest.json` | Row counts, validation summary, and SHA-256 checksums |

The `local_path` columns are project-relative provenance references. The raw
PDF files and parser intermediates are intentionally not committed. They can be
recreated with the ingestion commands documented in the repository root
README.
