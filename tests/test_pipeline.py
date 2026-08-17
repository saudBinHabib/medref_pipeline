"""SPEC.md §9 — full end-to-end run; idempotency; broken-feed handling."""

import csv
from pathlib import Path
from unittest.mock import MagicMock

from sqlalchemy import text

from src.config import Settings
from src.pipeline import main, run_pipeline

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


def test_failed_run_records_failed_status_with_partial_counts(clean_db, monkeypatch):
    """SPEC.md §5.6: on failure, status='failed' and partial counts collected
    so far are still recorded (rows_in from a completed ingest; rows_out=0
    since load never succeeded; content_hash left NULL since nothing loaded).
    """

    def _boom(session, records):
        raise RuntimeError("simulated load failure")

    monkeypatch.setattr("src.pipeline.load_batch", _boom)

    exit_code = run_pipeline(DATA_DIR / "feed_v1.csv")

    assert exit_code == 1
    run_row = clean_db.execute(
        text(
            "SELECT status, rows_in, rows_out, rows_rejected, content_hash "
            "FROM pipeline_runs"
        )
    ).one()
    assert run_row.status == "failed"
    assert run_row.rows_in == 80
    assert run_row.rows_out == 0
    assert run_row.rows_rejected == 0
    assert run_row.content_hash is None

    # The run must never have touched the serving table.
    total = clean_db.execute(text("SELECT COUNT(*) FROM medications")).scalar_one()
    assert total == 0


def test_main_downloads_s3_feed_path_before_running_pipeline(monkeypatch):
    """An `s3://bucket/key` --feed argument is downloaded to a local temp file
    via boto3 before the pipeline runs on it (ECS Fargate deployment target:
    the real feed lives in a "raw-landing" S3 bucket).
    """
    mock_s3_client = MagicMock()
    mock_boto3_client = MagicMock(return_value=mock_s3_client)
    monkeypatch.setattr("src.pipeline.boto3.client", mock_boto3_client)

    captured_feed_path = {}

    def _fake_run_pipeline(feed_path, *args, **kwargs):
        captured_feed_path["path"] = feed_path
        return 0

    monkeypatch.setattr("src.pipeline.run_pipeline", _fake_run_pipeline)

    exit_code = main(["--feed", "s3://raw-landing-bucket/feeds/feed_v1.csv"])

    assert exit_code == 0
    mock_boto3_client.assert_called_once_with("s3")
    mock_s3_client.download_file.assert_called_once()
    call_args = mock_s3_client.download_file.call_args.args
    assert call_args[0] == "raw-landing-bucket"
    assert call_args[1] == "feeds/feed_v1.csv"
    # run_pipeline must have received the local downloaded path, not the URI.
    assert captured_feed_path["path"] != "s3://raw-landing-bucket/feeds/feed_v1.csv"
    assert isinstance(captured_feed_path["path"], Path)


def test_main_local_feed_path_never_touches_s3(monkeypatch, tmp_path):
    """A plain local --feed path (existing/unchanged behavior) must not
    trigger any boto3/S3 calls at all.
    """
    mock_boto3_client = MagicMock()
    monkeypatch.setattr("src.pipeline.boto3.client", mock_boto3_client)

    feed_file = tmp_path / "feed.csv"
    feed_file.write_text("pzn\n")

    captured_feed_path = {}

    def _fake_run_pipeline(feed_path, *args, **kwargs):
        captured_feed_path["path"] = feed_path
        return 0

    monkeypatch.setattr("src.pipeline.run_pipeline", _fake_run_pipeline)

    exit_code = main(["--feed", str(feed_file)])

    assert exit_code == 0
    mock_boto3_client.assert_not_called()
    assert captured_feed_path["path"] == feed_file


def test_dead_letter_uploaded_to_s3_when_bucket_configured(clean_db, monkeypatch):
    """SPEC.md dead-letter-not-abort philosophy extended to S3: when
    DEAD_LETTER_BUCKET is set, the run's final dead-letter CSV is uploaded to
    s3://{bucket}/{run_id}.csv after the run (success or failure path).
    """
    mock_s3_client = MagicMock()
    mock_boto3_client = MagicMock(return_value=mock_s3_client)
    monkeypatch.setattr("src.pipeline.boto3.client", mock_boto3_client)
    monkeypatch.setattr(
        "src.pipeline.get_settings",
        lambda: Settings(dead_letter_bucket="my-dead-letter-bucket"),
    )

    exit_code = run_pipeline(DATA_DIR / "feed_broken.csv")

    assert exit_code == 0
    run_id = clean_db.execute(text("SELECT run_id FROM pipeline_runs")).scalar_one()

    mock_boto3_client.assert_called_once_with("s3")
    mock_s3_client.upload_file.assert_called_once()
    call_args = mock_s3_client.upload_file.call_args.args
    assert call_args[0] == str(Path("dead_letter") / f"{run_id}.csv")
    assert call_args[1] == "my-dead-letter-bucket"
    assert call_args[2] == f"{run_id}.csv"


def test_no_s3_upload_attempted_when_dead_letter_bucket_unset(clean_db, monkeypatch):
    """Unset DEAD_LETTER_BUCKET (local dev / docker-compose, existing tests)
    must leave behavior unchanged: dead-letter stays local-only, no S3 call.
    """
    mock_boto3_client = MagicMock()
    monkeypatch.setattr("src.pipeline.boto3.client", mock_boto3_client)
    monkeypatch.setattr(
        "src.pipeline.get_settings",
        lambda: Settings(dead_letter_bucket=None),
    )

    exit_code = run_pipeline(DATA_DIR / "feed_v1.csv")

    assert exit_code == 0
    mock_boto3_client.assert_not_called()


def test_dead_letter_upload_failure_does_not_change_pipeline_result(clean_db, monkeypatch):
    """A broken S3 upload must be logged and swallowed, never crash the run
    or flip its success/failure result (matches this codebase's
    dead-letter-not-abort philosophy elsewhere).
    """
    mock_s3_client = MagicMock()
    mock_s3_client.upload_file.side_effect = RuntimeError("simulated S3 outage")
    monkeypatch.setattr("src.pipeline.boto3.client", MagicMock(return_value=mock_s3_client))
    monkeypatch.setattr(
        "src.pipeline.get_settings",
        lambda: Settings(dead_letter_bucket="my-dead-letter-bucket"),
    )

    exit_code = run_pipeline(DATA_DIR / "feed_v1.csv")

    assert exit_code == 0
    mock_s3_client.upload_file.assert_called_once()
