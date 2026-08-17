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
    # Exponential backoff: each successive delay must be strictly larger than
    # the last (a regression to constant-delay retries would slip past a bare
    # len(sleeps) assertion).
    assert sleeps[1] > sleeps[0]


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


def test_ensure_atc_reference_rows_excludes_code_absent_from_csv(clean_db):
    """C3 regression coverage: a code absent from data/atc_reference.csv must be
    excluded from atc_reference, not silently loaded — this is exactly the gap
    that let the unknown-ATC bug through undetected across 10 task reviews.
    """
    ensure_atc_reference_rows(clean_db, {"N02BE01", "Z99ZZ99"})
    clean_db.commit()

    rows = clean_db.execute(text("SELECT atc_code FROM atc_reference ORDER BY atc_code")).all()
    assert [r[0] for r in rows] == ["N02BE01"]


def test_ensure_atc_reference_rows_logs_warning_when_remote_flag_enabled_but_unresolved(
    clean_db, monkeypatch, caplog
):
    """I2: the ATC_REMOTE_LOOKUP flag is wired into ensure_atc_reference_rows.
    No production remote fetcher exists anywhere in this project, so when the
    flag is on and a code is still unresolved after the offline CSV, we log a
    warning and fall back to offline-only behavior instead of crashing.
    """
    from src import enrich

    class _RemoteEnabledSettings:
        atc_remote_lookup = True

    monkeypatch.setattr(enrich, "get_settings", lambda: _RemoteEnabledSettings())

    with caplog.at_level("WARNING"):
        enrich.ensure_atc_reference_rows(clean_db, {"Z99ZZ99"})
    clean_db.commit()

    rows = clean_db.execute(text("SELECT atc_code FROM atc_reference")).all()
    assert rows == []
    assert any("atc_remote_lookup" in record.message for record in caplog.records)
