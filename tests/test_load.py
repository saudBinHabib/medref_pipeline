"""SPEC.md §9 — atomic swap leaves a complete table; a mid-load failure leaves it intact."""

from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from src.load import load_batch, write_staging
from src.transform import CleanRecord


def _record(pzn, name, manufacturer_id, price="9.99"):
    return CleanRecord(
        pzn=pzn, name=name, active_ingredient="Paracetamol", dosage_form="tablet",
        strength="500mg", prescription_only=False, price=Decimal(price),
        manufacturer_id=manufacturer_id, atc_code=None,
    )


def _seed_manufacturer(session, name="Nordhealth Pharma") -> int:
    return session.execute(
        text("INSERT INTO manufacturers (name) VALUES (:name) RETURNING manufacturer_id"),
        {"name": name},
    ).scalar_one()


def test_write_staging_populates_staging_table(clean_db):
    manufacturer_id = _seed_manufacturer(clean_db)
    write_staging(clean_db, [_record("11111111", "First", manufacturer_id)])

    rows = clean_db.execute(text("SELECT pzn FROM medications_staging")).all()
    assert [r.pzn for r in rows] == ["11111111"]


def test_load_batch_populates_serving_table(clean_db):
    manufacturer_id = _seed_manufacturer(clean_db)
    records = [
        _record("11111111", "First", manufacturer_id),
        _record("22222222", "Second", manufacturer_id),
    ]

    load_batch(clean_db, records)
    clean_db.commit()

    rows = clean_db.execute(text("SELECT pzn, name FROM medications ORDER BY pzn")).all()
    assert [(r.pzn, r.name) for r in rows] == [("11111111", "First"), ("22222222", "Second")]


def test_load_batch_upserts_changed_rows_idempotently(clean_db):
    manufacturer_id = _seed_manufacturer(clean_db)
    load_batch(clean_db, [_record("11111111", "First", manufacturer_id)])
    clean_db.commit()

    load_batch(clean_db, [_record("11111111", "First Updated", manufacturer_id, price="19.99")])
    clean_db.commit()

    rows = clean_db.execute(text("SELECT pzn, name, price FROM medications")).all()
    assert len(rows) == 1
    assert rows[0].name == "First Updated"
    assert rows[0].price == Decimal("19.99")


def test_repeated_load_of_same_batch_is_idempotent(clean_db):
    manufacturer_id = _seed_manufacturer(clean_db)
    records = [_record("11111111", "First", manufacturer_id)]

    load_batch(clean_db, records)
    clean_db.commit()
    load_batch(clean_db, records)
    clean_db.commit()

    rows = clean_db.execute(text("SELECT pzn FROM medications")).all()
    assert len(rows) == 1


def test_mid_load_failure_leaves_prior_serving_table_intact(clean_db):
    manufacturer_id = _seed_manufacturer(clean_db)
    load_batch(clean_db, [_record("11111111", "Original", manufacturer_id)])
    clean_db.commit()

    bad_batch = [_record("22222222", "Should Not Land", manufacturer_id=999999)]
    with pytest.raises(IntegrityError):
        load_batch(clean_db, bad_batch)
    clean_db.rollback()

    rows = clean_db.execute(text("SELECT pzn, name FROM medications")).all()
    assert [(r.pzn, r.name) for r in rows] == [("11111111", "Original")]
