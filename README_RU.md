# Vietnam Trade Research

Ingestion регулярных публикаций Vietnam Customs: каталог, безопасная загрузка,
годовой выбор документов и raw-парсинг таблиц `2X/2N`.

[English](README_EN.md) · **Русский**

Подробные правила отбора, обработки и расчёта показателей описаны в
[METHODOLOGY_RU.md](METHODOLOGY_RU.md).

## Что реализовано

- POST-клиент для `GetTKHQInfo` с таймаутом, retry и exponential backoff;
- обход страниц до `RowCount` и сохранение каждого исходного JSON-ответа;
- дедупликация публикаций по `MA`;
- нормализация пустых строк и строкового `"null"`;
- единая модель редакций `preliminary`, `revised`, `final`;
- полный реестр и отдельная выборка серий `2X`, `2N`, `5X`, `5N` за
  `2023-01..2026-09` включительно;
- блокировка внутренних URL из сети `10.0.0.0/8` как кандидатов на скачивание.
- годовой selector с приоритетом `final → revised → preliminary`;
- parser таблиц `2X/2N` с координатами слов, raw-клетками и валидацией.

## Приоритеты документов

Основной пакет, скачиваемый по умолчанию:

- `5X` — экспорт по странам и товарным группам;
- `5N` — импорт по странам и товарным группам;
- `2X` — общий экспорт по товарным группам;
- `2N` — общий импорт по товарным группам.

Дополнительный пакет по флагу `--include-additional`:

- `3X` — экспорт предприятий FDI;
- `3N` — импорт предприятий FDI.

Серии `4`, `1X`, `1N` пока не скачиваются. Стандартное окно охватывает не
более десяти календарных лет: `2017-01..2026-09`.

## Формат HTTP-запроса

Сайт использует необычную комбинацию: тело — результат
`JSON.stringify(payload)`, то есть сырой JSON, а заголовок остаётся
`Content-Type: application/x-www-form-urlencoded; charset=UTF-8`. Это не
обычный form-urlencoded набор полей. Базовый payload:

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

`the_loai` намеренно равен строке `"0"`: пустая строка приводит к серверной
ошибке преобразования числа.

## Установка и запуск

Требуется Python 3.10 или новее. PDF извлекаются через `pdfplumber`, который
устанавливается вместе с проектом.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
```

## Готовый набор для анализа

Репозиторий содержит минимальный обработанный набор, достаточный для запуска
исследовательского блокнота без скачивания и повторного парсинга PDF:

```text
data/processed/vietnam_customs/
  trade_facts.csv
  documents.csv
  commodity_mapping_approved_2017_2026.csv
  commodity_dictionary_approved.csv
  schema.json
  manifest.json
```

После установки зависимостей блокнот можно открыть из корня проекта или из
каталога `notebooks`:

```bash
jupyter lab notebooks/russia_vietnam_trade_2017_2026_RU.ipynb
```

Он сам находит корень проекта и записывает пересоздаваемые таблицы и графики в
`outputs/vietnam_trade_2017_2026_extended/`. Локальные абсолютные пути не
используются. Каталоги `data/raw`, `outputs` и промежуточные файлы исключены из
Git; для совместной работы достаточно клонированного репозитория.

Полный discovery каталога и построение обоих реестров:

```bash
vietnam-customs discover
```

Более крупный размер страницы и пользовательские сетевые параметры:

```bash
vietnam-customs discover --take 100 --timeout 30 --max-retries 3
```

Каждый запуск сохраняет исходные ответы в timestamp-папку:

```text
data/raw/vietnam_customs/metadata/runs/<UTC timestamp>/
```

Реестры записываются в:

```text
data/raw/vietnam_customs/metadata/publications_registry.json
data/raw/vietnam_customs/metadata/series_2X_2N_5X_5N_2023-01_2026-09.json
```

Повторно собрать реестры из уже сохранённых ответов без обращения к сайту:

```bash
vietnam-customs registry \
  --raw-dir data/raw/vietnam_customs/metadata/runs/<UTC timestamp>
```

Альтернативный запуск без установки entry point:

```bash
PYTHONPATH=src python -m ingestion.vietnam_customs discover
```

## Тесты

```bash
PYTHONPATH=src python -m unittest discover -s tests -v
```

Тесты не обращаются к сети и не скачивают PDF.

## Скачивание документов

Сначала рекомендуется проверить выборку без сетевых запросов:

```bash
vietnam-customs download --dry-run
```

Скачать основной пакет `5X/5N/2X/2N` за `2017-01..2026-09`:

```bash
vietnam-customs download --workers 4
```

Добавить серии `3X/3N`:

```bash
vietnam-customs download --workers 4 --include-additional
```

Файлы раскладываются по схеме
`documents/<series>/<YYYY-MM>/<MA>/<edition>-<source filename>`. Повторный запуск
пропускает уже скачанные и прошедшие проверку файлы. Каждый результат сразу
добавляется в `documents/download_manifest.jsonl`, включая SHA-256, размер,
ошибки и отклонённые URL. Разрешён только точный хост `files.customs.gov.vn`;
внутренние, склеенные и неподдерживаемые адреса не запрашиваются.

## Годовой набор и парсинг PDF

Выбрать по одному документу на серию и год за `2017-01..2026-09`:

```bash
vietnam-customs select
```

Для закрытых лет selector предпочитает декабрь, для 2026 года — последний
доступный месяц. Внутри периода действует приоритет
`final → revised → preliminary`. Отсутствующий декабрь и fallback редакции
записываются явно в:

```text
data/raw/vietnam_customs/metadata/document_selection.jsonl
```

Проверить и скачать только выбранные 40 годовых документов:

```bash
vietnam-customs download-selected --dry-run
vietnam-customs download-selected --workers 4
```

Распарсить выбранные `2X/2N`:

```bash
vietnam-customs parse-summary
```

Распарсить выбранные `5X/5N` с переносом страны между страницами:

```bash
vietnam-customs parse-country
```

Собрать весь набор `2X/2N/5X/5N` одной командой и выгрузить CSV:

```bash
vietnam-customs build-dataset
```

Если PDF уже распарсены, пересобрать только CSV без повторного чтения PDF:

```bash
vietnam-customs export-csv
```

Результат:

```text
data/processed/vietnam_customs/
  trade_facts.csv  # все опубликованные строки 2X/2N/5X/5N
  documents.csv    # происхождение и статус 40 выбранных документов
  schema.json       # машинное описание колонок и типов
  manifest.json     # число строк, пропуски и контрольные суммы файлов
```

В `trade_facts.csv` экспорт/импорт задаётся полем `trade_flow`, год и период —
полями `reference_year`, `reference_month`, `publication_period`. Для строк
`2X/2N` страна пустая и `fact_level` начинается с `national_`; для `5X/5N`
заполнена `country_name_raw`, а уровень равен `country_total` или
`country_commodity`.

Для каждого PDF создаются:

```text
data/raw/vietnam_customs/extracted/
  parser=summary-v1/series=<series>/period=<YYYY-MM>/ma=<MA>/edition=<edition>/
    document.json
    pages.jsonl
    rows.jsonl
    validation.json
```

Для `5X/5N` используется аналогичная структура под `parser=country-v1`.

Числа в JSON записываются каноническими десятичными строками, чтобы не терять
точность. Исходные строки, клетки таблицы, текст страниц и координаты слов
сохраняются рядом. Схема готового аналитического слоя хранится в
`data/processed/vietnam_customs/schema.json`.
