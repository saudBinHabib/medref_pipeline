"""SPEC.md §9 — every FeedRow validation rule, valid + each invalid case."""

from decimal import Decimal

import pytest
from pydantic import ValidationError

from src.schema import FeedRow

VALID_ROW = {
    "pzn": "12345678",
    "name": "Testodol",
    "active_ingredient": "Paracetamol",
    "dosage_form": "tablet",
    "strength": "500mg",
    "prescription_only": "false",
    "price": "9.99",
    "manufacturer": "Nordhealth Pharma",
    "atc_code": "N02BE01",
}


def test_valid_row_parses():
    row = FeedRow.model_validate(VALID_ROW)
    assert row.pzn == "12345678"
    assert row.prescription_only is False
    assert row.price == Decimal("9.99")
    assert row.atc_code == "N02BE01"


def test_pzn_preserves_leading_zeros():
    row = FeedRow.model_validate({**VALID_ROW, "pzn": "00012345"})
    assert row.pzn == "00012345"


@pytest.mark.parametrize(
    "overrides,expected_fragment",
    [
        ({"name": ""}, "name"),
        ({"pzn": "ABCDEFGH"}, "pzn"),
        ({"pzn": "123"}, "pzn"),
        ({"dosage_form": "lozenge"}, "dosage_form"),
        ({"prescription_only": "maybe"}, "prescription_only"),
        ({"price": "-5.00"}, "price"),
        ({"atc_code": "not-a-code"}, "atc_code"),
    ],
)
def test_invalid_rows_rejected_with_reason(overrides, expected_fragment):
    with pytest.raises(ValidationError) as exc_info:
        FeedRow.model_validate({**VALID_ROW, **overrides})
    assert expected_fragment in str(exc_info.value)


@pytest.mark.parametrize(
    "value,expected",
    [
        ("true", True), ("1", True), ("yes", True), ("YES", True),
        ("false", False), ("0", False), ("no", False), ("NO", False),
    ],
)
def test_prescription_only_accepts_boolean_like_values(value, expected):
    row = FeedRow.model_validate({**VALID_ROW, "prescription_only": value})
    assert row.prescription_only is expected


def test_atc_code_optional():
    row = FeedRow.model_validate({**VALID_ROW, "atc_code": ""})
    assert row.atc_code is None
