"""Safe, resumable downloader for selected Vietnam Customs document series."""

from __future__ import annotations

import hashlib
import json
import os
import random
import re
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable, Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import unquote, urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .client import RETRYABLE_STATUS_CODES
from .registry import ADDITIONAL_SERIES, CORE_SERIES, is_internal_10_url


ALLOWED_HOST = "files.customs.gov.vn"
ALLOWED_EXTENSIONS = frozenset({".pdf", ".xls", ".xlsx"})
NESTED_URL_PATTERN = re.compile(r"https?://", re.IGNORECASE)


class DownloadError(RuntimeError):
    """Raised when a document cannot be downloaded safely."""


class UnsafeRedirectError(DownloadError):
    """Raised before following a redirect outside the download allowlist."""


def validate_download_url(url: str | None) -> tuple[bool, str | None]:
    """Validate scheme, exact host, path and extension without resolving DNS."""
    if not url:
        return False, "missing_url"
    if is_internal_10_url(url):
        return False, "internal_10_url"
    parsed = urlsplit(url)
    if parsed.scheme.lower() not in {"http", "https"}:
        return False, "unsupported_scheme"
    if (parsed.hostname or "").lower() != ALLOWED_HOST:
        return False, "host_not_allowed"
    decoded_path = unquote(parsed.path)
    if NESTED_URL_PATTERN.search(decoded_path):
        return False, "nested_or_malformed_url"
    if Path(decoded_path).suffix.lower() not in ALLOWED_EXTENSIONS:
        return False, "unsupported_extension"
    return True, None


class SafeRedirectHandler(HTTPRedirectHandler):
    """Refuse a redirect before urllib connects to a non-allowlisted target."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        absolute = urljoin(req.full_url, newurl)
        allowed, reason = validate_download_url(absolute)
        if not allowed:
            raise UnsafeRedirectError(f"Unsafe redirect ({reason}): {absolute}")
        return super().redirect_request(req, fp, code, msg, headers, absolute)


@dataclass(frozen=True)
class DownloadCandidate:
    key: str
    ma: str
    series: str
    period: str
    edition: str
    url: str
    destination: Path


@dataclass(frozen=True)
class DownloadResult:
    key: str
    ma: str
    series: str
    period: str
    edition: str
    url: str
    destination: str
    status: str
    size_bytes: int | None = None
    sha256: str | None = None
    reason: str | None = None


def _safe_filename(value: str) -> str:
    value = re.sub(r"[^A-Za-z0-9._()\-]+", "_", value).strip("._")
    return value[:180] or "document"


def document_destination(
    output_directory: Path,
    *,
    series: str,
    period: str,
    ma: str,
    edition: str,
    url: str,
) -> Path:
    """Return the canonical local path for a catalogue document."""
    filename = _safe_filename(unquote(Path(urlsplit(url).path).name))
    return output_directory / series / period / ma / f"{edition}-{filename}"


def collect_candidates(
    registry: Iterable[dict[str, Any]],
    *,
    output_directory: Path,
    include_additional: bool = False,
    start: str = "2017-01",
    end: str = "2026-09",
) -> tuple[list[DownloadCandidate], list[DownloadResult]]:
    """Create download jobs and explicit rejection records from a registry."""
    if start > end:
        raise ValueError("start must not be after end")
    selected = set(CORE_SERIES)
    if include_additional:
        selected.update(ADDITIONAL_SERIES)

    candidates: list[DownloadCandidate] = []
    rejected: list[DownloadResult] = []
    seen: set[str] = set()
    for publication in registry:
        period = publication.get("publication_period")
        if not period or not start <= period <= end:
            continue
        ma = str(publication["ma"])
        for edition, item in (publication.get("editions") or {}).items():
            series = item.get("series")
            if series not in selected:
                continue
            url = item.get("source_url") or ""
            key = f"{ma}:{edition}:{series}"
            if key in seen:
                continue
            seen.add(key)
            destination = document_destination(
                output_directory,
                series=series,
                period=period,
                ma=ma,
                edition=edition,
                url=url,
            )
            allowed, reason = validate_download_url(url)
            if not allowed:
                rejected.append(
                    DownloadResult(
                        key=key,
                        ma=ma,
                        series=series,
                        period=period,
                        edition=edition,
                        url=url,
                        destination=str(destination),
                        status="invalid_url",
                        reason=reason,
                    )
                )
                continue
            candidates.append(
                DownloadCandidate(
                    key=key,
                    ma=ma,
                    series=series,
                    period=period,
                    edition=edition,
                    url=url,
                    destination=destination,
                )
            )
    return candidates, rejected


def _hash_file(path: Path) -> tuple[int, str, bytes]:
    digest = hashlib.sha256()
    size = 0
    prefix = b""
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            if not prefix:
                prefix = chunk[:8]
            size += len(chunk)
            digest.update(chunk)
    return size, digest.hexdigest(), prefix


def _valid_signature(extension: str, prefix: bytes) -> bool:
    if extension == ".pdf":
        return prefix.startswith(b"%PDF-")
    if extension == ".xls":
        return prefix.startswith(bytes.fromhex("D0CF11E0A1B11AE1"))
    if extension == ".xlsx":
        return prefix.startswith(b"PK\x03\x04")
    return False


class DocumentDownloader:
    def __init__(
        self,
        *,
        timeout: float = 60.0,
        max_retries: int = 3,
        backoff_factor: float = 1.0,
        max_bytes: int = 100 * 1024 * 1024,
        opener: Any | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if timeout <= 0 or max_bytes <= 0 or max_retries < 0:
            raise ValueError("invalid downloader limits")
        self.timeout = timeout
        self.max_retries = max_retries
        self.backoff_factor = backoff_factor
        self.max_bytes = max_bytes
        self._opener = opener or build_opener(SafeRedirectHandler())
        self._sleep = sleep

    def download(self, candidate: DownloadCandidate) -> DownloadResult:
        destination = candidate.destination
        destination.parent.mkdir(parents=True, exist_ok=True)
        extension = destination.suffix.lower()

        if destination.exists():
            size, digest, prefix = _hash_file(destination)
            if _valid_signature(extension, prefix):
                return self._result(candidate, "skipped", size, digest, "already_exists")
            destination.unlink()

        partial = destination.with_suffix(destination.suffix + ".part")
        partial.unlink(missing_ok=True)
        for attempt in range(self.max_retries + 1):
            try:
                request = Request(
                    candidate.url,
                    headers={
                        "Accept": "application/pdf, application/vnd.ms-excel, "
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet, */*",
                        "Referer": "https://www.customs.gov.vn/index.jsp?pageId=5002",
                        "User-Agent": "vietnam-trade-research/0.1 (document downloader)",
                    },
                )
                with self._opener.open(request, timeout=self.timeout) as response:
                    final_url = response.geturl()
                    allowed, reason = validate_download_url(final_url)
                    if not allowed:
                        raise UnsafeRedirectError(f"Unsafe final URL ({reason}): {final_url}")
                    content_length = response.headers.get("Content-Length")
                    if content_length and int(content_length) > self.max_bytes:
                        raise DownloadError("content_too_large")
                    digest = hashlib.sha256()
                    size = 0
                    prefix = b""
                    with partial.open("wb") as output:
                        while chunk := response.read(1024 * 1024):
                            if not prefix:
                                prefix = chunk[:8]
                            size += len(chunk)
                            if size > self.max_bytes:
                                raise DownloadError("content_too_large")
                            digest.update(chunk)
                            output.write(chunk)
                if not _valid_signature(extension, prefix):
                    raise DownloadError("invalid_file_signature")
                os.replace(partial, destination)
                return self._result(candidate, "downloaded", size, digest.hexdigest())
            except HTTPError as exc:
                retryable = exc.code in RETRYABLE_STATUS_CODES
                if not retryable or attempt >= self.max_retries:
                    partial.unlink(missing_ok=True)
                    return self._result(candidate, "failed", reason=f"http_{exc.code}")
            except (URLError, TimeoutError, socket.timeout) as exc:
                if attempt >= self.max_retries:
                    partial.unlink(missing_ok=True)
                    return self._result(candidate, "failed", reason=type(exc).__name__)
            except (DownloadError, UnsafeRedirectError, ValueError) as exc:
                partial.unlink(missing_ok=True)
                return self._result(candidate, "failed", reason=str(exc))
            delay = self.backoff_factor * (2**attempt)
            jitter = random.uniform(0, self.backoff_factor / 4) if delay else 0
            self._sleep(delay + jitter)
        raise AssertionError("retry loop exited unexpectedly")

    @staticmethod
    def _result(
        candidate: DownloadCandidate,
        status: str,
        size: int | None = None,
        digest: str | None = None,
        reason: str | None = None,
    ) -> DownloadResult:
        return DownloadResult(
            key=candidate.key,
            ma=candidate.ma,
            series=candidate.series,
            period=candidate.period,
            edition=candidate.edition,
            url=candidate.url,
            destination=str(candidate.destination),
            status=status,
            size_bytes=size,
            sha256=digest,
            reason=reason,
        )


def download_candidates(
    candidates: Iterable[DownloadCandidate],
    *,
    downloader: DocumentDownloader,
    manifest_path: Path,
    workers: int = 4,
    initial_results: Iterable[DownloadResult] = (),
) -> list[DownloadResult]:
    """Download concurrently and append every outcome to a JSONL manifest."""
    if workers <= 0 or workers > 16:
        raise ValueError("workers must be between 1 and 16")
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    results: list[DownloadResult] = []
    lock = threading.Lock()

    def record(result: DownloadResult) -> None:
        with lock:
            with manifest_path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(asdict(result), ensure_ascii=False) + "\n")
            results.append(result)

    for result in initial_results:
        record(result)
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(downloader.download, item): item for item in candidates}
        for future in as_completed(futures):
            try:
                record(future.result())
            except Exception as exc:  # preserve an outcome for every job
                item = futures[future]
                record(downloader._result(item, "failed", reason=repr(exc)))
    return results
