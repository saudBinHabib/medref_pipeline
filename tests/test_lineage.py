"""src/lineage.py in isolation — start_run/finish_run and content_hash determinism
(SPEC.md §5.6). Complements the end-to-end lineage assertions in test_pipeline.py.
"""

from decimal import Decimal

from sqlalchemy import text

from src.lineage import content_hash, finish_run, start_run
from src.transform import CleanRecord


def _record(pzn, name="Alpha", manufacturer_id=1, atc_code=None, price="9.99"):
    return CleanRecord(
        pzn=pzn, name=name, active_ingredient="Paracetamol", dosage_form="tablet",
        strength="500mg", prescription_only=False, price=Decimal(price),
        manufacturer_id=manufacturer_id, atc_code=atc_code,
    )


# --- content_hash: determinism -------------------------------------------


def test_content_hash_is_order_independent():
    a = _record("11111111", "Alpha")
    b = _record("22222222", "Beta")

    assert content_hash([a, b]) == content_hash([b, a])


def test_content_hash_is_deterministic_across_calls():
    records = [_record("11111111", "Alpha"), _record("22222222", "Beta")]

    assert content_hash(records) == content_hash(records)


def test_content_hash_differs_when_a_field_changes():
    original = [_record("11111111", "Alpha", price="9.99")]
    changed = [_record("11111111", "Alpha", price="19.99")]

    assert content_hash(original) != content_hash(changed)


def test_content_hash_differs_when_row_set_changes():
    one_row = [_record("11111111", "Alpha")]
    two_rows = [_record("11111111", "Alpha"), _record("22222222", "Beta")]

    assert content_hash(one_row) != content_hash(two_rows)


def test_content_hash_of_empty_batch_is_stable():
    assert content_hash([]) == content_hash([])


# --- start_run / finish_run in isolation ----------------------------------


def test_start_run_inserts_a_running_row(clean_db):
    run_id = start_run(clean_db, source_file="data/feed_v1.csv")
    clean_db.commit()

    row = clean_db.execute(
        text(
            "SELECT source_file, status, started_at, finished_at, "
            "rows_in, rows_out, rows_rejected, content_hash "
            "FROM pipeline_runs WHERE run_id = :run_id"
        ),
        {"run_id": run_id},
    ).one()
    assert row.source_file == "data/feed_v1.csv"
    assert row.status == "running"
    assert row.started_at is not None
    assert row.finished_at is None
    assert row.rows_in is None
    assert row.rows_out is None
    assert row.rows_rejected is None
    assert row.content_hash is None


def test_finish_run_updates_counts_status_and_hash_on_success(clean_db):
    run_id = start_run(clean_db, source_file="data/feed_v1.csv")
    clean_db.commit()

    finish_run(
        clean_db, run_id,
        rows_in=10, rows_out=9, rows_rejected=1,
        status="success", content_hash_value="deadbeef",
    )
    clean_db.commit()

    row = clean_db.execute(
        text(
            "SELECT status, rows_in, rows_out, rows_rejected, content_hash, finished_at "
            "FROM pipeline_runs WHERE run_id = :run_id"
        ),
        {"run_id": run_id},
    ).one()
    assert row.status == "success"
    assert row.rows_in == 10
    assert row.rows_out == 9
    assert row.rows_rejected == 1
    assert row.content_hash == "deadbeef"
    assert row.finished_at is not None


def test_finish_run_records_failed_status_with_null_content_hash(clean_db):
    """SPEC.md §5.6: on failure, status='failed' and partial counts collected
    so far are still recorded; content_hash stays NULL since nothing loaded.
    """
    run_id = start_run(clean_db, source_file="data/feed_broken.csv")
    clean_db.commit()

    finish_run(
        clean_db, run_id,
        rows_in=11, rows_out=0, rows_rejected=9,
        status="failed", content_hash_value=None,
    )
    clean_db.commit()

    row = clean_db.execute(
        text(
            "SELECT status, rows_in, rows_out, rows_rejected, content_hash "
            "FROM pipeline_runs WHERE run_id = :run_id"
        ),
        {"run_id": run_id},
    ).one()
    assert row.status == "failed"
    assert row.rows_in == 11
    assert row.rows_out == 0
    assert row.rows_rejected == 9
    assert row.content_hash is None
