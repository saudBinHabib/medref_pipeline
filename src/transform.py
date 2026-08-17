"""Deduplicate + normalize manufacturer names (SPEC.md §5.3)."""

from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.orm import Session

from src.schema import FeedRow


@dataclass
class CleanRecord:
    pzn: str
    name: str
    active_ingredient: str
    dosage_form: str
    strength: str
    prescription_only: bool
    price: Decimal
    manufacturer_id: int
    atc_code: str | None


def deduplicate(rows: list[FeedRow]) -> list[FeedRow]:
    """Keep the last occurrence of each pzn; preserve first-seen order otherwise."""
    last_by_pzn: dict[str, FeedRow] = {}
    order: list[str] = []
    for row in rows:
        if row.pzn not in last_by_pzn:
            order.append(row.pzn)
        last_by_pzn[row.pzn] = row
    return [last_by_pzn[pzn] for pzn in order]


def resolve_manufacturer_id(session: Session, name: str) -> int:
    """Look up manufacturer by name, inserting if new. Returns manufacturer_id."""
    existing = session.execute(
        text("SELECT manufacturer_id FROM manufacturers WHERE name = :name"),
        {"name": name},
    ).first()
    if existing is not None:
        return existing[0]
    inserted = session.execute(
        text(
            "INSERT INTO manufacturers (name) VALUES (:name) "
            "ON CONFLICT (name) DO UPDATE SET name = EXCLUDED.name "
            "RETURNING manufacturer_id"
        ),
        {"name": name},
    ).first()
    return inserted[0]


def normalize(session: Session, rows: list[FeedRow]) -> list[CleanRecord]:
    """Resolve manufacturer_id for each row. Call after deduplicate()."""
    return [
        CleanRecord(
            pzn=row.pzn,
            name=row.name,
            active_ingredient=row.active_ingredient,
            dosage_form=row.dosage_form,
            strength=row.strength,
            prescription_only=row.prescription_only,
            price=row.price,
            manufacturer_id=resolve_manufacturer_id(session, row.manufacturer),
            atc_code=row.atc_code,
        )
        for row in rows
    ]
