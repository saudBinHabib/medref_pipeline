"""SPEC.md §9 — full end-to-end run; idempotency; broken-feed handling."""

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
        text("SELECT rows_in, rows_out, rows_rejected FROM pipeline_runs")
    ).one()
    assert run_row.rows_in == 8
    assert run_row.rows_rejected == 6
    assert run_row.rows_out == 1

    total = clean_db.execute(text("SELECT COUNT(*) FROM medications")).scalar_one()
    assert total == 1
