"""enrich.py — offline ATC lookup, remote retry with backoff + Retry-After (SPEC.md §5.4)."""

import pytest
from sqlalchemy import text

from src.enrich import (
    AtcLookupExhausted,
    RetryableAtcError,
    ensure_atc_reference_rows,
    load_atc_reference_csv,
    lookup_atc,
    resolve_atc_codes,
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


def test_ensure_atc_reference_rows_upserts_resolved_codes(clean_db):
    ensure_atc_reference_rows(
        clean_db, {"N02BE01": "Paracetamol", "M01AE01": "Ibuprofen"}
    )
    clean_db.commit()

    rows = clean_db.execute(text("SELECT atc_code FROM atc_reference ORDER BY atc_code")).all()
    assert [r[0] for r in rows] == ["M01AE01", "N02BE01"]


def test_ensure_atc_reference_rows_is_a_pure_persistence_step(clean_db):
    """ensure_atc_reference_rows no longer decides resolvability itself
    (I2/duplication cleanup) — it just upserts whatever `resolved` dict it is
    given. An empty `resolved` dict (e.g. because resolve_atc_codes could not
    resolve anything) upserts nothing.
    """
    ensure_atc_reference_rows(clean_db, {})
    clean_db.commit()

    rows = clean_db.execute(text("SELECT atc_code FROM atc_reference")).all()
    assert rows == []


# --- resolve_atc_codes: single source of truth for ATC resolution ---------


def test_resolve_atc_codes_offline_only_resolves_from_reference_dict():
    reference = load_atc_reference_csv()

    resolved, unresolved = resolve_atc_codes({"N02BE01", "Z99ZZ99"}, reference)

    assert resolved == {"N02BE01": reference["N02BE01"]}
    assert unresolved == {"Z99ZZ99"}


def test_resolve_atc_codes_does_not_use_remote_fallback_when_flag_off():
    """C3/I2 regression: with `remote=False` (ATC_REMOTE_LOOKUP off), an
    unresolved code must stay unresolved even if a fetch_fn could resolve
    it — the flag must actually gate the behavior, not just be decorative.
    """

    def fetch(code):
        return "should never be called"

    resolved, unresolved = resolve_atc_codes(
        {"Z99ZZ99"}, {}, remote=False, fetch_fn=fetch
    )

    assert resolved == {}
    assert unresolved == {"Z99ZZ99"}


def test_resolve_atc_codes_remote_fallback_resolves_code_missing_from_csv():
    """I2: proves the ATC_REMOTE_LOOKUP flag actually changes behavior — a
    code absent from the offline reference dict is resolved via a fake
    fetch_fn when `remote=True`, and excluded when `remote=False`.
    """

    def fake_fetch(code):
        return {"Z99ZZ99": "Fake Zzz Compound"}.get(code)

    resolved, unresolved = resolve_atc_codes(
        {"Z99ZZ99"}, {}, remote=True, fetch_fn=fake_fetch
    )

    assert resolved == {"Z99ZZ99": "Fake Zzz Compound"}
    assert unresolved == set()


def test_resolve_atc_codes_remote_fallback_still_unresolved_when_fetch_fn_misses():
    def fake_fetch(code):
        return None

    resolved, unresolved = resolve_atc_codes(
        {"Z99ZZ99"}, {}, remote=True, fetch_fn=fake_fetch
    )

    assert resolved == {}
    assert unresolved == {"Z99ZZ99"}


def test_resolve_atc_codes_remote_fallback_treats_exhausted_retries_as_unresolved(caplog):
    def always_retryable(code):
        raise RetryableAtcError()

    with caplog.at_level("WARNING"):
        resolved, unresolved = resolve_atc_codes(
            {"Z99ZZ99"},
            {},
            remote=True,
            fetch_fn=always_retryable,
        )

    assert resolved == {}
    assert unresolved == {"Z99ZZ99"}
    assert any("exhausted" in record.message for record in caplog.records)


def test_resolve_atc_codes_prefers_offline_reference_over_remote():
    """A code present in the offline CSV must never trigger a remote call."""

    def fetch(code):
        raise AssertionError("remote fetch_fn should not be called for an offline hit")

    resolved, unresolved = resolve_atc_codes(
        {"N02BE01"}, {"N02BE01": "Paracetamol"}, remote=True, fetch_fn=fetch
    )

    assert resolved == {"N02BE01": "Paracetamol"}
    assert unresolved == set()


def test_default_remote_fetch_fn_wired_as_default_when_no_fetch_fn_given(monkeypatch):
    """I2: when ATC_REMOTE_LOOKUP is on and the caller doesn't inject a
    fetch_fn, resolve_atc_codes must fall back to enrich.default_remote_fetch_fn
    (the real integration point), not silently skip remote resolution.
    """
    from src import enrich

    calls = []

    def fake_default(code):
        calls.append(code)
        return "Resolved By Default Fetcher"

    monkeypatch.setattr(enrich, "default_remote_fetch_fn", fake_default)

    resolved, unresolved = enrich.resolve_atc_codes({"Z99ZZ99"}, {}, remote=True)

    assert calls == ["Z99ZZ99"]
    assert resolved == {"Z99ZZ99": "Resolved By Default Fetcher"}
    assert unresolved == set()
