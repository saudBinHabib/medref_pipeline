"""ATC enrichment + retry-wrapped remote lookup (SPEC.md §5.4)."""

from __future__ import annotations

import csv
import logging
import random
import time
from collections.abc import Callable
from pathlib import Path

import httpx
from sqlalchemy import text
from sqlalchemy.orm import Session

from src.config import get_settings

# Resolved relative to this module's file, not the process cwd, so it works
# regardless of where the pipeline/tests are invoked from.
ATC_REFERENCE_CSV = Path(__file__).resolve().parent.parent / "data" / "atc_reference.csv"
MAX_RETRIES = 5
BASE_BACKOFF_SECONDS = 0.5

logger = logging.getLogger(__name__)


class AtcLookupExhausted(Exception):
    """Raised when a remote ATC lookup exhausts its retry budget."""


class RetryableAtcError(Exception):
    """Raised by a remote fetch function to request a retry.

    `retry_after`, when set, overrides the exponential backoff delay
    (mirrors an HTTP `Retry-After` header).
    """

    def __init__(self, retry_after: float | None = None):
        self.retry_after = retry_after
        super().__init__(f"retryable ATC lookup error (retry_after={retry_after})")


def load_atc_reference_csv(path: Path = ATC_REFERENCE_CSV) -> dict[str, str]:
    with open(path, newline="", encoding="utf-8") as fh:
        return {row["atc_code"]: row["atc_description"] for row in csv.DictReader(fh)}


def lookup_atc(
    code: str,
    *,
    remote: bool = False,
    fetch_fn: Callable[[str], str | None] | None = None,
    sleep_fn: Callable[[float], None] = time.sleep,
    max_retries: int = MAX_RETRIES,
) -> str | None:
    """Return the ATC description for `code`, or None if unknown.

    Offline (`remote=False`, default): reads data/atc_reference.csv.
    Remote (`remote=True`): calls `fetch_fn(code)`, retrying on
    `RetryableAtcError` with exponential backoff + jitter (or the error's
    `retry_after`, if set) up to `max_retries` attempts.
    """
    if not remote:
        return load_atc_reference_csv().get(code)
    if fetch_fn is None:
        raise ValueError("fetch_fn is required when remote=True")

    attempt = 0
    while True:
        try:
            return fetch_fn(code)
        except RetryableAtcError as retry:
            attempt += 1
            if attempt > max_retries:
                raise AtcLookupExhausted(
                    f"exhausted {max_retries} retries looking up ATC code {code!r}"
                ) from retry
            delay = retry.retry_after
            if delay is None:
                delay = (BASE_BACKOFF_SECONDS * (2 ** (attempt - 1))) + random.uniform(
                    0, BASE_BACKOFF_SECONDS
                )
            sleep_fn(delay)


def default_remote_fetch_fn(code: str) -> str | None:
    """Best-effort remote ATC terminology lookup, used as the default
    `fetch_fn` when `ATC_REMOTE_LOOKUP=true` (SPEC.md §5.4).

    SPEC.md asks for `lookup_atc` to be "modeled behind a function ... so it
    can later point at a remote terminology API", gated by a configurable
    flag, but does not name a concrete API — no such service is otherwise
    integrated in this project. This hits a configurable HTTP endpoint
    (`ATC_REMOTE_API_URL`, `GET {url}/{code}` returning
    `{"atc_description": "..."}`) as a documented, swappable integration
    point: point it at a real WHO ATC terminology service in production
    without touching `lookup_atc`'s retry/backoff wrapper.

    Network errors, 429, and 5xx responses are retryable (raise
    `RetryableAtcError`, honoring a `Retry-After` header on 429); a 404 means
    "code not found upstream" and returns None, same as an offline miss.
    """
    settings = get_settings()
    url = f"{settings.atc_remote_api_url.rstrip('/')}/{code}"
    try:
        response = httpx.get(url, timeout=settings.atc_remote_timeout_seconds)
    except httpx.HTTPError as exc:
        raise RetryableAtcError() from exc

    if response.status_code == 404:
        return None
    if response.status_code == 429:
        retry_after = response.headers.get("Retry-After")
        raise RetryableAtcError(retry_after=float(retry_after) if retry_after else None)
    if response.status_code >= 500:
        raise RetryableAtcError()
    response.raise_for_status()
    return response.json().get("atc_description")


def resolve_atc_codes(
    codes: set[str],
    reference_descriptions: dict[str, str],
    *,
    remote: bool = False,
    fetch_fn: Callable[[str], str | None] | None = None,
) -> tuple[dict[str, str], set[str]]:
    """Resolve every `code` to its `atc_description`.

    Single source of truth for ATC-code resolution: `reference_descriptions`
    is `data/atc_reference.csv` loaded exactly once by the caller (SPEC.md
    §5.4) — this function never re-reads the file itself. A code missing
    from the offline reference is, when `remote` is true, given one more
    chance via `lookup_atc(code, remote=True, fetch_fn=fetch_fn)` (defaults
    to `default_remote_fetch_fn`) before being reported unresolved.

    Returns `(resolved, unresolved)`: `resolved` maps every code that could
    be resolved (offline or remote) to its description; `unresolved` is the
    set of codes no source could resolve. Callers own what happens to a row
    referencing an unresolved code (SPEC.md §5.1/§7: dead-letter it rather
    than let it abort the batch via an FK violation) — this function only
    resolves, it never decides what to do with a row.
    """
    resolved: dict[str, str] = {}
    unresolved: set[str] = set()
    for code in sorted(codes):
        description = reference_descriptions.get(code)
        if description is None and remote:
            try:
                description = lookup_atc(
                    code, remote=True, fetch_fn=fetch_fn or default_remote_fetch_fn
                )
            except AtcLookupExhausted:
                logger.warning(
                    "remote ATC lookup exhausted its retry budget for code=%s; "
                    "treating as unresolved",
                    code,
                )
                description = None
        if description is None:
            unresolved.add(code)
        else:
            resolved[code] = description
    return resolved, unresolved


def ensure_atc_reference_rows(session: Session, resolved: dict[str, str]) -> None:
    """Upsert already-resolved `{atc_code: atc_description}` pairs into `atc_reference`.

    Called before load so `medications.atc_code` FK resolves. Callers build
    `resolved` via `resolve_atc_codes` — this function only persists, it does
    not decide resolvability (single source of truth: `resolve_atc_codes`).
    """
    for code, description in sorted(resolved.items()):
        session.execute(
            text(
                "INSERT INTO atc_reference (atc_code, atc_description) "
                "VALUES (:code, :description) "
                "ON CONFLICT (atc_code) DO NOTHING"
            ),
            {"code": code, "description": description},
        )
