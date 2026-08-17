"""enrich.py — offline ATC lookup, remote retry with backoff + Retry-After (SPEC.md §5.4)."""

import pytest
from sqlalchemy import text

from src.enrich import (
    AtcLookupExhausted,
    RetryableAtcError,
    ensure_atc_reference_rows,
    lookup_atc,
)


def test_offline_lookup_reads_local_csv():
    assert lookup_atc("N02BE01", remote=False) == "Paracetamol"


def test_offline_lookup_unknown_code_returns_none():
    assert lookup_atc("Z99ZZ99", remote=False) is None


def test_remote_lookup_retries_then_succeeds():
    attempts = {"count": 0}

    def flaky_fetch(code):
        attempts["count"] += 1
        if attempts["count"] < 3:
            raise RetryableAtcError()
        return "Paracetamol"

    sleeps = []
    result = lookup_atc(
        "N02BE01", remote=True, fetch_fn=flaky_fetch, sleep_fn=sleeps.append, max_retries=5
    )

    assert result == "Paracetamol"
    assert attempts["count"] == 3
    assert len(sleeps) == 2


def test_remote_lookup_honors_retry_after():
    def fetch(code):
        raise RetryableAtcError(retry_after=2.5)

    sleeps = []
    with pytest.raises(AtcLookupExhausted):
        lookup_atc("N02BE01", remote=True, fetch_fn=fetch, sleep_fn=sleeps.append, max_retries=2)

    assert sleeps == [2.5, 2.5]


def test_remote_lookup_raises_after_max_retries():
    def always_fails(code):
        raise RetryableAtcError()

    with pytest.raises(AtcLookupExhausted):
        lookup_atc(
            "N02BE01", remote=True, fetch_fn=always_fails, sleep_fn=lambda _: None, max_retries=2
        )


def test_ensure_atc_reference_rows_upserts_missing_codes(clean_db):
    ensure_atc_reference_rows(clean_db, {"N02BE01", "M01AE01"})
    clean_db.commit()

    rows = clean_db.execute(text("SELECT atc_code FROM atc_reference ORDER BY atc_code")).all()
    assert [r[0] for r in rows] == ["M01AE01", "N02BE01"]
