"""SPEC.md §9 — dedup keeps the correct record; manufacturer normalization is stable."""

from decimal import Decimal

from src.schema import FeedRow
from src.transform import deduplicate, normalize, resolve_manufacturer_id


def _row(pzn, name, manufacturer="Nordhealth Pharma"):
    return FeedRow.model_validate({
        "pzn": pzn, "name": name, "active_ingredient": "Paracetamol",
        "dosage_form": "tablet", "strength": "500mg", "prescription_only": "false",
        "price": "9.99", "manufacturer": manufacturer, "atc_code": "N02BE01",
    })


def test_deduplicate_keeps_last_occurrence():
    rows = [_row("11111111", "First"), _row("22222222", "Other"), _row("11111111", "Last")]

    result = deduplicate(rows)

    assert len(result) == 2
    by_pzn = {r.pzn: r for r in result}
    assert by_pzn["11111111"].name == "Last"


def test_deduplicate_preserves_first_seen_order():
    rows = [_row("22222222", "A"), _row("11111111", "B"), _row("22222222", "C")]

    result = deduplicate(rows)

    assert [r.pzn for r in result] == ["22222222", "11111111"]


def test_resolve_manufacturer_id_is_stable(clean_db):
    first = resolve_manufacturer_id(clean_db, "Nordhealth Pharma")
    second = resolve_manufacturer_id(clean_db, "Nordhealth Pharma")
    assert first == second


def test_resolve_manufacturer_id_inserts_new(clean_db):
    manufacturer_id = resolve_manufacturer_id(clean_db, "Brand New Labs")
    assert isinstance(manufacturer_id, int)


def test_normalize_resolves_manufacturer_ids(clean_db):
    rows = [
        _row("11111111", "First", "Nordhealth Pharma"),
        _row("22222222", "Second", "Nordhealth Pharma"),
    ]

    records = normalize(clean_db, rows)

    assert records[0].manufacturer_id == records[1].manufacturer_id
    assert records[0].price == Decimal("9.99")
