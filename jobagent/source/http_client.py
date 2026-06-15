"""PoliteClient — a polite, cached, rate-limited HTTP client.

Every outbound fetch in the sourcing stage goes through this client so the
guardrails are enforced in one place:

  * a descriptive User-Agent
  * per-host rate limiting (a minimum gap between requests to the same host)
  * an on-disk response cache with a TTL (don't re-hammer boards)
  * robots.txt checking for HTML scraping (sanctioned JSON APIs are exempt,
    since they are explicitly provided for programmatic access)
  * bounded retries with backoff for transient failures
"""

from __future__ import annotations

import hashlib
import time
import urllib.robotparser
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from ..config import SourcingConfig
from ..logging_setup import get_logger
from ..util import from_json, now_iso, to_json

log = get_logger("source.http")


class RobotsDisallowed(Exception):
    """Raised when robots.txt disallows a path we were asked to scrape."""


@dataclass
class Response:
    url: str
    status: int
    text: str
    from_cache: bool
    cache_path: str | None


class PoliteClient:
    def __init__(
        self,
        sourcing: SourcingConfig,
        cache_dir: Path,
        client: httpx.Client | None = None,
    ) -> None:
        self.cfg = sourcing
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._client = client or httpx.Client(
            timeout=sourcing.request_timeout,
            headers={"User-Agent": sourcing.user_agent},
            follow_redirects=True,
        )
        self._owns_client = client is None
        self._last_hit: dict[str, float] = {}
        self._robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}

    # -- lifecycle ---------------------------------------------------------- #
    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> "PoliteClient":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # -- public API --------------------------------------------------------- #
    def get(self, url: str, *, sanctioned_api: bool = False, force: bool = False) -> Response:
        """Fetch a URL politely. Returns cached content when fresh.

        `sanctioned_api=True` marks official JSON endpoints (Greenhouse/Lever)
        which are provided for programmatic use and are exempt from robots.txt.
        """
        cached = None if force else self._read_cache(url)
        if cached is not None:
            return cached

        if not sanctioned_api and self.cfg.respect_robots and not self._robots_allows(url):
            raise RobotsDisallowed(f"robots.txt disallows fetching {url}")

        self._respect_rate_limit(url)
        text, status = self._fetch_with_retries(url)
        cache_path = self._write_cache(url, status, text)
        return Response(url=url, status=status, text=text, from_cache=False, cache_path=cache_path)

    def get_json(self, url: str, *, sanctioned_api: bool = True, force: bool = False):
        import json

        resp = self.get(url, sanctioned_api=sanctioned_api, force=force)
        return json.loads(resp.text)

    # -- caching ------------------------------------------------------------ #
    def _cache_file(self, url: str) -> Path:
        digest = hashlib.sha1(url.encode("utf-8")).hexdigest()
        return self.cache_dir / f"{digest}.json"

    def _read_cache(self, url: str) -> Response | None:
        path = self._cache_file(url)
        if not path.exists():
            return None
        record = from_json(path.read_text(encoding="utf-8"), {})
        fetched_at = record.get("fetched_at")
        if not fetched_at:
            return None
        age = time.time() - record.get("epoch", 0)
        if age > self.cfg.cache_ttl_seconds:
            return None
        return Response(
            url=url,
            status=record.get("status", 0),
            text=record.get("body", ""),
            from_cache=True,
            cache_path=str(path),
        )

    def _write_cache(self, url: str, status: int, text: str) -> str:
        path = self._cache_file(url)
        path.write_text(
            to_json(
                {
                    "url": url,
                    "status": status,
                    "fetched_at": now_iso(),
                    "epoch": time.time(),
                    "body": text,
                }
            ),
            encoding="utf-8",
        )
        return str(path)

    # -- politeness --------------------------------------------------------- #
    def _respect_rate_limit(self, url: str) -> None:
        host = urlsplit(url).netloc
        last = self._last_hit.get(host)
        if last is not None:
            wait = self.cfg.min_interval_seconds - (time.monotonic() - last)
            if wait > 0:
                time.sleep(wait)
        self._last_hit[host] = time.monotonic()

    def _robots_allows(self, url: str) -> bool:
        parts = urlsplit(url)
        host = parts.netloc
        if host not in self._robots:
            self._robots[host] = self._load_robots(parts.scheme, host)
        parser = self._robots[host]
        if parser is None:  # couldn't load robots.txt -> default allow
            return True
        return parser.can_fetch(self.cfg.user_agent, url)

    def _load_robots(self, scheme: str, host: str):
        robots_url = f"{scheme}://{host}/robots.txt"
        try:
            resp = self._client.get(robots_url)
            if resp.status_code >= 400:
                return None
            parser = urllib.robotparser.RobotFileParser()
            parser.parse(resp.text.splitlines())
            return parser
        except httpx.HTTPError as exc:
            log.debug("robots.txt fetch failed for %s: %s", host, exc)
            return None

    # -- fetch with retries ------------------------------------------------- #
    def _fetch_with_retries(self, url: str) -> tuple[str, int]:
        last_exc: Exception | None = None
        for attempt in range(1, self.cfg.max_retries + 1):
            try:
                resp = self._client.get(url)
                if resp.status_code == 429 or resp.status_code >= 500:
                    retry_after = _retry_after_seconds(resp)
                    log.warning(
                        "%s -> HTTP %s (attempt %d/%d)",
                        url, resp.status_code, attempt, self.cfg.max_retries,
                    )
                    self._backoff(attempt, retry_after)
                    last_exc = httpx.HTTPStatusError(
                        f"HTTP {resp.status_code}", request=resp.request, response=resp
                    )
                    continue
                resp.raise_for_status()
                return resp.text, resp.status_code
            except (httpx.TransportError, httpx.TimeoutException) as exc:
                log.warning("%s -> %s (attempt %d/%d)", url, exc, attempt, self.cfg.max_retries)
                last_exc = exc
                self._backoff(attempt, None)
        raise last_exc or RuntimeError(f"failed to fetch {url}")

    def _backoff(self, attempt: int, retry_after: float | None) -> None:
        delay = retry_after if retry_after is not None else min(2 ** attempt, 30)
        time.sleep(delay)


def _retry_after_seconds(resp: httpx.Response) -> float | None:
    value = resp.headers.get("Retry-After")
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        return None
