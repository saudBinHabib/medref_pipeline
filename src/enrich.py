"""ATC enrichment + retry-wrapped remote lookup (SPEC.md §5.4)."""

from __future__ import annotations

import csv
import random
import time
from collections.abc import Callable
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.orm import Session

# Resolved relative to this module's file, not the process cwd, so it works
# regardless of where the pipeline/tests are invoked from.
ATC_REFERENCE_CSV = Path(__file__).resolve().parent.parent / "data" / "atc_reference.csv"
MAX_RETRIES = 5
BASE_BACKOFF_SECONDS = 0.5


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


def ensure_atc_reference_rows(session: Session, codes: set[str]) -> None:
    """Upsert data/atc_reference.csv rows for every ATC code referenced in a feed.

    Called before load so medications.atc_code FK resolves.
    """
    if not codes:
        return
    descriptions = load_atc_reference_csv()
    for code in sorted(codes):
        description = descriptions.get(code)
        if description is None:
            continue
        session.execute(
            text(
                "INSERT INTO atc_reference (atc_code, atc_description) "
                "VALUES (:code, :description) "
                "ON CONFLICT (atc_code) DO NOTHING"
            ),
            {"code": code, "description": description},
        )
