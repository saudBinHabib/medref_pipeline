"""Pydantic v2 schema contracts for feed rows and served medications (SPEC.md §5.2)."""

import re
from decimal import Decimal, InvalidOperation

from pydantic import BaseModel, ConfigDict, field_validator

ALLOWED_DOSAGE_FORMS = {"tablet", "capsule", "solution", "injection", "cream", "drops", "spray"}
ATC_CODE_PATTERN = re.compile(r"^[A-Z]\d{2}[A-Z]{2}\d{2}$")
_TRUE_VALUES = {"true", "1", "yes"}
_FALSE_VALUES = {"false", "0", "no"}


class FeedRow(BaseModel):
    """Raw incoming feed row, validated per SPEC.md §5.2."""

    model_config = ConfigDict(str_strip_whitespace=True)

    pzn: str
    name: str
    active_ingredient: str
    dosage_form: str
    strength: str
    prescription_only: bool
    price: Decimal
    manufacturer: str
    atc_code: str | None = None

    @field_validator("pzn")
    @classmethod
    def validate_pzn(cls, v: str) -> str:
        if not v.isdigit() or len(v) != 8:
            raise ValueError(f"pzn must be exactly 8 numeric characters, got {v!r}")
        return v

    @field_validator("name", "active_ingredient", "strength", "manufacturer")
    @classmethod
    def validate_non_empty(cls, v: str, info) -> str:
        if not v:
            raise ValueError(f"{info.field_name} must not be empty")
        return v

    @field_validator("dosage_form")
    @classmethod
    def validate_dosage_form(cls, v: str) -> str:
        if v not in ALLOWED_DOSAGE_FORMS:
            raise ValueError(
                f"dosage_form must be one of {sorted(ALLOWED_DOSAGE_FORMS)}, got {v!r}"
            )
        return v

    @field_validator("prescription_only", mode="before")
    @classmethod
    def validate_prescription_only(cls, v: object) -> bool:
        if isinstance(v, bool):
            return v
        s = str(v).strip().lower()
        if s in _TRUE_VALUES:
            return True
        if s in _FALSE_VALUES:
            return False
        raise ValueError(
            f"prescription_only must be a boolean-like value (true/false/1/0/yes/no), got {v!r}"
        )

    @field_validator("price", mode="before")
    @classmethod
    def validate_price(cls, v: object) -> Decimal:
        try:
            price = Decimal(str(v))
        except InvalidOperation as exc:
            raise ValueError(f"price must be a valid decimal number, got {v!r}") from exc
        if price < 0:
            raise ValueError(f"price must be >= 0, got {price}")
        return price

    @field_validator("atc_code")
    @classmethod
    def validate_atc_code(cls, v: str | None) -> str | None:
        if v is None or v == "":
            return None
        if not ATC_CODE_PATTERN.match(v):
            raise ValueError(f"atc_code must match ATC format (e.g. 'M01AE01'), got {v!r}")
        return v


class Medication(BaseModel):
    """Clean/serving model returned by the API (SPEC.md §5.2, §6)."""

    model_config = ConfigDict(from_attributes=True)

    pzn: str
    name: str
    active_ingredient: str
    dosage_form: str
    strength: str
    prescription_only: bool
    price: Decimal
    manufacturer_id: int
    atc_code: str | None = None
