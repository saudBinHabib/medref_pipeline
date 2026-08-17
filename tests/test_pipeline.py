"""SPEC.md §9 — full end-to-end run; idempotency; broken-feed handling."""

import csv
from pathlib import Path

from sqlalchemy import text

from src.pipeline import run_pipeline

DATA_DIR = Path(__file__).resolve().parent.parent / "data"


def _medications_snapshot(session):
    rows = session.execute(
        text(
            "SELECT pzn, name, active_ingredient, dosage_form, strength, "
            "       prescription_only, price, manufacturer_id, atc_code "
            "FROM medications ORDER BY pzn"
        )
    ).all()
    return [tuple(r) for r in rows]


def test_full_run_on_feed_v1_loads_serving_table(clean_db):
    exit_code = run_pipeline(DATA_DIR / "feed_v1.csv")

    assert exit_code == 0
    total = clean_db.execute(text("SELECT COUNT(*) FROM medications")).scalar_one()
    assert total == 80

    run_row = clean_db.execute(
        text("SELECT status, rows_in, rows_out, rows_rejected FROM pipeline_runs")
    ).one()
    assert run_row.status == "success"
    assert run_row.rows_in == 80
    assert run_row.rows_out == 80
    assert run_row.rows_rejected == 0


def test_running_same_feed_twice_is_idempotent(clean_db):
    run_pipeline(DATA_DIR / "feed_v1.csv")
    first_snapshot = _medications_snapshot(clean_db)

    run_pipeline(DATA_DIR / "feed_v1.csv")
    second_snapshot = _medications_snapshot(clean_db)

    assert first_snapshot == second_snapshot


def test_delta_feed_upserts_new_and_changed_rows(clean_db):
    run_pipeline(DATA_DIR / "feed_v1.csv")
    before_total = clean_db.execute(text("SELECT COUNT(*) FROM medications")).scalar_one()

    exit_code = run_pipeline(DATA_DIR / "feed_v2_delta.csv")

    assert exit_code == 0
    after_total = clean_db.execute(text("SELECT COUNT(*) FROM medications")).scalar_one()
    assert after_total == before_total + 10  # 10 brand-new pzns; 10 changed rows updated in place

    forte_count = clean_db.execute(
        text("SELECT COUNT(*) FROM medications WHERE name LIKE :pattern"),
        {"pattern": "% Forte"},
    ).scalar_one()
    assert forte_count == 10


def test_broken_feed_rejects_bad_rows_and_loads_valid_ones(clean_db):
    exit_code = run_pipeline(DATA_DIR / "feed_broken.csv")

    assert exit_code == 0
    run_row = clean_db.execute(
        text("SELECT run_id, rows_in, rows_out, rows_rejected FROM pipeline_runs")
    ).one()
    # 11 rows total: 6 schema-invalid (missing field, non-numeric pzn, wrong-length
    # pzn, bad dosage_form, non-boolean prescription_only, negative price) + 2
    # duplicate-pzn rows (schema-valid, one dropped by dedup, not by dead-letter)
    # + 1 unknown-atc_code row (schema-valid, rejected at the pipeline level) + 1
    # oversized price + 1 NaN price (both now schema-invalid, C3). rows_rejected
    # covers everything dead-lettered: 6 + 1 (unknown atc) + 2 (price) = 9.
    assert run_row.rows_in == 11
    assert run_row.rows_rejected == 9
    assert run_row.rows_out == 1

    total = clean_db.execute(text("SELECT COUNT(*) FROM medications")).scalar_one()
    assert total == 1

    # Dead-letter file must record the reason for the unknown-atc_code rejection
    # (C3: an unresolvable-but-well-formed atc_code must be dead-lettered, not
    # allowed to abort the whole batch via a downstream FK violation).
    dead_letter_path = Path("dead_letter") / f"{run_row.run_id}.csv"
    with open(dead_letter_path, newline="", encoding="utf-8") as fh:
        reasons = [row["rejection_reason"] for row in csv.DictReader(fh)]
    assert any("atc_code: unknown code 'Z99ZZ99'" in r for r in reasons)
    assert len(reasons) == 9
