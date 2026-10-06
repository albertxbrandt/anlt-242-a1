"""The only module that performs HTTP.

It sets the User-Agent, waits between live requests, retries transient
failures, and stores raw response bodies. Collectors ask for a cache key
instead of writing their own request code.
"""

from dataclasses import dataclass
import json
import time
from pathlib import Path

import requests
from tqdm import tqdm

from src.config import (
    MAX_RETRIES,
    RAW_DIR,
    REQUEST_TIMEOUT_SECONDS,
    USER_AGENT,
)


class FetchError(RuntimeError):
    """A request failed after every retry."""


@dataclass(frozen=True)
class FetchResult:
    """One finished GET.

    ``status_code`` is the HTTP status. ``text`` is the body, empty when the
    status is not 200. ``from_cache`` is true when no network call was made.
    """

    status_code: int
    text: str
    from_cache: bool


class HttpClient:
    """GET helper with a file cache and a polite delay."""

    def __init__(self, cache_dir: Path = RAW_DIR) -> None:
        self.cache_dir = cache_dir
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT, "Accept-Language": "en-US,en"})

    def get_json(self, url: str, cache_key: str, delay_seconds: float) -> dict:
        """Return a JSON object. Raises ``FetchError`` if the body is not JSON or the status is not 200.

        ``cache_key`` is a relative path under the raw cache, such as
        ``details/730.json``. ``delay_seconds`` applies only when the file is
        not already cached.
        """

        result = self.fetch(url, cache_key, delay_seconds)
        if result.status_code != 200:
            raise FetchError(f"{url} returned HTTP {result.status_code}")
        try:
            return json.loads(result.text)
        except json.JSONDecodeError as exc:
            raise FetchError(f"{url} did not return JSON") from exc

    def fetch(self, url: str, cache_key: str, delay_seconds: float) -> FetchResult:
        """Return the response body for ``url``, using the cache when it exists.

        404 is returned immediately. 429 is retried with a long wait. A 5xx
        response is tried twice and then returned so the caller can decide
        whether that page is missing.
        """

        cache_path = self._cache_path(cache_key)
        if cache_path.exists():
            return FetchResult(200, cache_path.read_text(encoding="utf-8"), True)

        rate_limits = 0
        connection_errors = 0
        server_errors = 0
        last_error: Exception | None = None
        while True:
            try:
                response = self.session.get(url, timeout=REQUEST_TIMEOUT_SECONDS)
            except requests.RequestException as exc:
                connection_errors += 1
                last_error = exc
                if connection_errors >= 3:
                    raise FetchError(f"{url} failed after connection errors") from last_error
                time.sleep(5 * connection_errors)
                continue

            if response.status_code == 200:
                text = response.text
                cache_path.parent.mkdir(parents=True, exist_ok=True)
                cache_path.write_text(text, encoding="utf-8")
                time.sleep(delay_seconds)
                return FetchResult(200, text, False)

            # 404 is a normal miss. A repeated 500 is returned to the caller so
            # one app with no chart does not stall the whole run.
            if response.status_code == 404:
                time.sleep(delay_seconds)
                return FetchResult(404, "", False)

            if response.status_code == 429:
                rate_limits += 1
                last_error = FetchError(f"{url} returned HTTP 429")
                if rate_limits >= MAX_RETRIES:
                    raise FetchError(f"{url} was rate limited too many times") from last_error
                self._backoff(rate_limits - 1, delay_seconds, response)
                continue

            if response.status_code >= 500:
                server_errors += 1
                if server_errors >= 2:
                    time.sleep(delay_seconds)
                    return FetchResult(response.status_code, "", False)
                time.sleep(5)
                continue

            time.sleep(delay_seconds)
            return FetchResult(response.status_code, response.text, False)

    def _cache_path(self, cache_key: str) -> Path:
        """Resolve a cache key and refuse keys that escape the cache directory."""

        path = (self.cache_dir / cache_key).resolve()
        if self.cache_dir.resolve() not in path.parents:
            raise ValueError(f"Cache key escapes the cache directory: {cache_key}")
        return path

    def _backoff(self, attempt: int, delay_seconds: float, response: requests.Response | None = None) -> None:
        """Wait longer after each failed try. A 429 waits at least 20 seconds.

        Steam's store search returns 429 when pages are requested too quickly.
        The Retry-After header is used when it is present and longer than the
        default wait.
        """

        wait = delay_seconds * (2 ** attempt)
        if response is not None and response.status_code == 429:
            wait = max(wait, 20 * (attempt + 1))
            retry_after = response.headers.get("Retry-After") if response is not None else None
            if retry_after and retry_after.isdigit():
                wait = max(wait, int(retry_after))
        if wait >= 20:
            tqdm.write(f"Rate limited. Waiting {wait:.0f} seconds.")
        time.sleep(wait)
