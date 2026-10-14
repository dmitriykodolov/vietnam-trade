# Methodology for the Russia–Vietnam Trade Study

**English** · [Русский](METHODOLOGY_RU.md) · [Project overview](README_EN.md)

## 1. Purpose and scope

The study describes the scale, commodity structure, and evolution of trade
between Russia and Vietnam from 2017 through 2026. It also assesses Russia's
position in Vietnam's import market. The latest available observation covers
January–September 2026 and is labelled `2026 YTD`.

The analysis addresses five questions:

1. How did Russia's exports, imports, turnover, and trade balance change?
2. Which broad and detailed commodity categories make up bilateral trade?
3. Which categories made the largest accounting contribution to value changes?
4. How diversified are the two bilateral trade flows?
5. What share does Russia hold in comparable segments of Vietnam's import
   market?

All monetary values are expressed in current US dollars. No inflation or
exchange-rate adjustment is applied.

## 2. Data source

The primary source is the scheduled English-language publications of the
General Department of Vietnam Customs. The publication catalogue is collected
through the public `GetTKHQInfo` endpoint, while source documents are
downloaded from `files.customs.gov.vn`.

Four publication series are used:

| Series | Publication contents | Interpretation for Russia |
| --- | --- | --- |
| `2X` | Vietnam exports by commodity group | Vietnam's total export market |
| `2N` | Vietnam imports by commodity group | Vietnam's total import market |
| `5X` | Vietnam exports by country and commodity | Russia's imports from Vietnam |
| `5N` | Vietnam imports by country and commodity | Russia's exports to Vietnam |

The processed dataset retains each source URL, the `MA` publication identifier,
period, edition, document hash, parser version, and validation status. Every
analytical row can therefore be traced back to a source publication.

## 3. Publication selection

One document is selected for each series and reporting year.

- For completed years, the December publication is preferred.
- For 2026, the latest available period, September, is used.
- Within the selected period, editions are prioritized as
  `final → revised → preliminary`.
- If December or the final edition is unavailable, the best available document
  is used and the fallback reason is retained in the metadata.

The current dataset contains 40 documents: four series for ten reporting
years. Observations for 2017–2025 are full-year cumulative values; the 2026
observation is cumulative for January–September.

## 4. Downloading and raw storage

The downloader accepts only `http` or `https` URLs whose exact host is
`files.customs.gov.vn` and whose extension is PDF, XLS, or XLSX. It also checks:

- internal and incorrectly concatenated URLs;
- every HTTP redirect;
- the maximum response size;
- the signature of the downloaded file;
- file size and SHA-256.

A repeated run does not download an existing file again if its signature is
valid. Original catalogue responses, source documents, and parser
intermediates are stored locally under `data/raw/` and are excluded from Git.

## 5. Table extraction

### 5.1. Series 2X/2N

National export and import tables are extracted with `pdfplumber`. Original
page text and word coordinates are preserved for every page. Table rows are
classified as:

- `total` — national total;
- `fdi_total` — foreign-invested enterprise total;
- `commodity` — commodity row;
- `other` — another published row type.

The parser normalizes a historical layout variation containing an empty first
column. It preserves both normalized numbers and the original cell values.

### 5.2. Series 5X/5N

Country tables are parsed from word coordinates and detected column
boundaries. The active country is carried across page boundaries, and wrapped
commodity names are joined. The result contains two row levels:

- `country_total` — the official total for a country;
- `country_commodity` — the published commodity detail for that country.

Historical publications may provide incomplete commodity detail. The sum of
`country_commodity` rows must therefore not replace the official
`country_total`.

### 5.3. Numbers and missing values

Numbers are initially stored as canonical decimal strings without thousands
separators. This prevents premature precision loss. Missing values become empty
CSV fields. Percentage measures published by the source are retained rather
than recalculated.

## 6. Data validation

Document-level validation checks:

- table code, reporting period, and edition;
- the presence of total and commodity rows;
- commodity-number uniqueness and continuity for 2X/2N;
- the presence of country totals and absence of orphan commodity rows for
  5X/5N;
- duplicate countries;
- reconciliation of a country total to its detailed rows when a balancing
  `Other products` row is present, with a USD 5 tolerance for source rounding.

All 40 documents in the current dataset have a `passed` validation status. The
final fact layer contains 25,108 rows. Row counts, SHA-256 checksums, and the
schema version are recorded in
`data/processed/vietnam_customs/manifest.json`.

## 7. Analytical levels

### 7.1. Official totals

Russia's exports, imports, turnover, and trade balance use only
`country_total` rows for `Russian Federation`:

```text
Russia exports = 5N country_total
Russia imports = 5X country_total
trade turnover = exports + imports
Russia balance = exports − imports
```

### 7.2. Commodity harmonization

Commodity labels changed across publication years. Comparability is created
with a two-step model:

1. the source pair `(series, commodity_name_raw)` is mapped to a
   `commodity_id`;
2. `commodity_id` is linked to a canonical L2 name and a broader L1 analytical
   group.

L2 is an internally harmonized commodity category, while L1 is a broad
analytical grouping. Neither is an official HS code. HS attributes in the
dictionary are included only as descriptive reference fields.

All 23,499 national and country commodity rows in the current version are
mapped. After L2 aggregation, the values reconcile exactly to the mapped raw
rows.

`Other products` remains a published category. `Unclassified` (`C074`) remains
in the processed source layer but is excluded from concentration analysis,
strategy matrices, and the market-opportunity screen.

## 8. Metric definitions

### 8.1. Shares and detail coverage

A category's share of published commodity detail is:

```text
share_i = value_i / Σ value_i
```

Coverage of an official country total by published commodity rows is:

```text
coverage = Σ country_commodity / country_total
```

The category share describes the structure of published detail and not
necessarily the structure of the complete official country total.

### 8.2. CAGR and trajectory stability

CAGR uses full years only, from 2017 through 2025, which gives eight annual
intervals:

```text
CAGR = (value_2025 / value_2017)^(1/8) − 1
```

CAGR is not calculated if the initial or final value is zero, negative, or
missing. L1 and L2 analysis also reports:

- absolute value change;
- the number of growth and decline years out of eight;
- the standard deviation of annual growth rates (`YoY volatility`);
- whether all nine annual observations are present.

“Sustained growth” means positive CAGR and at least six growth years in a
complete series. “Sustained decline” is defined symmetrically. This is an
analytical rule, not a statistical significance test.

Low bases are flagged separately: below USD 10 million for L1, below USD
1 million for L2, and below USD 100 million for Vietnam's import market.

### 8.3. Concentration

Concentration is calculated from L2 shares within published detail:

```text
HHI = Σ share_i²
HHI_10000 = HHI × 10,000
effective categories = 1 / HHI
CR5 = sum of the five largest L2 shares
```

Higher HHI and CR5 indicate greater concentration. The effective number of
categories expresses the observed distribution as the equivalent number of
equal-sized categories.

### 8.4. Contribution and strategy matrix

An L2 category's contribution to trade change is the difference between its
2017 value and its 2026 YTD value. This is an accounting decomposition, not a
causal estimate.

The strategy matrix compares absolute value change with the change in a
category's share of the relevant bilateral flow. Categories are assigned to
four groups: strengthening position, growth with declining share, contraction
with rising share, and weakening position.

## 9. Russia's position in Vietnam's market

Vietnam's import market is constructed from national `2N` commodity rows.
Russian supply is constructed from country-level `5N` commodity rows. The two
are joined on the same `commodity_id`:

```text
Russia share = Vietnam imports from Russia / total Vietnam imports
```

The aggregate share covers matched L2 categories only. It must not be treated
as Russia's share of all Vietnamese imports.

The formal screen for categories requiring further research uses 2025 data. A
category qualifies when:

- Vietnam's 2025 import market is at least USD 0.5 billion;
- its 2017 base is at least USD 0.1 billion;
- market CAGR for 2017–2025 is positive;
- Russia's 2025 share is below 2%;
- observations exist for every year from 2017 through 2025.

This screen generates research hypotheses, not export recommendations. It does
not account for margins, logistics, production capacity, sanctions, tariffs,
non-tariff measures, or competitor behavior.

## 10. Period comparability

Values for 2017–2025 cover full years, while 2026 covers January–September
only. Therefore:

- 2026 YTD is displayed as a separate incomplete period;
- 2026 YTD is excluded from CAGR;
- 2026 YTD is not compared with full-year 2025 as a valid YoY comparison;
- changes from 2017 to 2026 YTD are used only as descriptive comparisons of
  two published snapshots and are labelled explicitly as YTD.

## 11. Japan benchmark

Japan is processed in the same way as Russia: `country_commodity` rows are
selected from 5X/5N and aggregated to L1. The comparison is a benchmark for
commodity specialization. It does not demonstrate a Russian logistics
advantage or the feasibility of directly replacing Japanese supply.

## 12. Interpretation limitations

- Vietnam Customs data may differ from Russian mirror statistics because of
  methodology, timing, re-exports, and country-of-origin rules.
- Commodity detail in 5X/5N does not always exhaust the official country total.
- L1/L2 are analytical harmonizations and necessarily simplify the original
  commodity labels.
- Trade value does not separate physical-volume changes from price changes.
- Nominal dollars from different years are not converted to constant prices.
- Observed changes do not establish causality.
- Results depend on the completeness and correctness of the published PDFs.

## 13. Reproducibility

The ready-to-use processed dataset and dictionaries are stored in
`data/processed/vietnam_customs/`. Reproducing the analysis requires:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
jupyter lab notebooks/russia_vietnam_trade_2017_2026_EN.ipynb
```

Rebuild the analytical layer from locally downloaded source documents with:

```bash
vietnam-customs build-dataset
```

The notebook verifies dictionary-key uniqueness, complete commodity mapping,
and exact preservation of values during L2 aggregation. Generated tables and
charts are written to `outputs/vietnam_trade_2017_2026_extended/` and are not
committed to Git.
