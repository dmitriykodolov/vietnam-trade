"""HTTP client for the Vietnam Customs scheduled-publications endpoint."""

from __future__ import annotations

import json
import random
import socket
import time
from http.cookiejar import CookieJar
from typing import Any, Callable, Mapping
from urllib.error import HTTPError, URLError
from urllib.request import HTTPCookieProcessor, Request, build_opener


DEFAULT_ENDPOINT = (
    "https://www.customs.gov.vn/bridge?url=/customs/api/GetTKHQInfo"
)
DEFAULT_REFERER = "https://www.customs.gov.vn/index.jsp?pageId=5002"
RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})


class VietnamCustomsError(RuntimeError):
    """Base exception for the Vietnam Customs client."""


class VietnamCustomsResponseError(VietnamCustomsError):
    """Raised when the endpoint returns an unexpected response."""


class VietnamCustomsClient:
    """Small dependency-free client with bounded retries and backoff.

    The website's JavaScript calls ``JSON.stringify(payload)`` but does not set
    a JSON content type.  Consequently the server expects a raw JSON document
    labelled as ``application/x-www-form-urlencoded``.  Sending form fields or
    using ``application/json`` is not equivalent to the browser request.
    """

    def __init__(
        self,
        endpoint: str = DEFAULT_ENDPOINT,
        *,
        timeout: float = 30.0,
        max_retries: int = 3,
        backoff_factor: float = 0.75,
        opener: Any | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        if max_retries < 0:
            raise ValueError("max_retries cannot be negative")
        if backoff_factor < 0:
            raise ValueError("backoff_factor cannot be negative")

        self.endpoint = endpoint
        self.timeout = timeout
        self.max_retries = max_retries
        self.backoff_factor = backoff_factor
        self._opener = opener or build_opener(HTTPCookieProcessor(CookieJar()))
        self._sleep = sleep

    @staticmethod
    def build_payload(
        *,
        skip: int,
        take: int,
        ky: str = "",
        text_search: str = "",
        the_loai: str = "0",
        thoigian_cong_bo: str = "",
        language: str = "TIENG_ANH",
    ) -> dict[str, Any]:
        if skip < 0:
            raise ValueError("skip cannot be negative")
        if take <= 0:
            raise ValueError("take must be positive")
        return {
            "skip": skip,
            "take": take,
            "ky": ky,
            "textSearch": text_search,
            "the_loai": the_loai,
            "thoigianCongBo": thoigian_cong_bo,
            "typeName": "GetListSoLieu",
            "language": language,
        }

    def fetch_page(self, *, skip: int, take: int, **filters: Any) -> dict[str, Any]:
        """Fetch and validate one catalogue page."""
        payload = self.build_payload(skip=skip, take=take, **filters)
        return self.post_payload(payload)

    def post_payload(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        """POST a browser-compatible raw JSON body and return decoded JSON."""
        body = json.dumps(
            dict(payload), ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
        headers = {
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "Origin": "https://www.customs.gov.vn",
            "Referer": DEFAULT_REFERER,
            "User-Agent": "vietnam-trade-research/0.1 (+catalogue metadata only)",
            "X-Requested-With": "XMLHttpRequest",
        }

        for attempt in range(self.max_retries + 1):
            request = Request(self.endpoint, data=body, headers=headers, method="POST")
            try:
                with self._opener.open(request, timeout=self.timeout) as response:
                    raw = response.read()
                return self._decode_response(raw)
            except HTTPError as exc:
                if exc.code not in RETRYABLE_STATUS_CODES or attempt >= self.max_retries:
                    raise VietnamCustomsError(
                        f"Vietnam Customs HTTP error {exc.code}"
                    ) from exc
            except (URLError, TimeoutError, socket.timeout) as exc:
                if attempt >= self.max_retries:
                    raise VietnamCustomsError(
                        "Vietnam Customs request failed after retries"
                    ) from exc

            delay = self.backoff_factor * (2**attempt)
            jitter = random.uniform(0, self.backoff_factor / 4) if delay else 0
            self._sleep(delay + jitter)

        raise AssertionError("retry loop exited unexpectedly")

    @staticmethod
    def _decode_response(raw: bytes) -> dict[str, Any]:
        try:
            decoded = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            preview = raw[:160].decode("utf-8", errors="replace").replace("\n", " ")
            raise VietnamCustomsResponseError(
                f"Vietnam Customs returned non-JSON content: {preview!r}"
            ) from exc

        if not isinstance(decoded, dict):
            raise VietnamCustomsResponseError("Expected a JSON object from Vietnam Customs")
        if decoded.get("message") == "Invalid Captcha":
            raise VietnamCustomsResponseError("Vietnam Customs rejected the request captcha")
        if "RowCount" not in decoded or "arr" not in decoded:
            raise VietnamCustomsResponseError(
                "Response is missing required fields 'arr' and/or 'RowCount'"
            )
        if decoded["arr"] is not None and not isinstance(decoded["arr"], list):
            raise VietnamCustomsResponseError("Response field 'arr' is not a list")
        return decoded
