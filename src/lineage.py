"""Run manifest / lineage tracking (SPEC.md §5.6)."""

import hashlib
import uuid
from datetime import UTC, datetime

from sqlalchemy import text
from sqlalchemy.orm import Session

from src.transform import CleanRecord


def start_run(session: Session, source_file: str) -> uuid.UUID:
    run_id = uuid.uuid4()
    session.execute(
        text(
            "INSERT INTO pipeline_runs (run_id, source_file, started_at, status) "
            "VALUES (:run_id, :source_file, :started_at, 'running')"
        ),
        {"run_id": run_id, "source_file": source_file, "started_at": datetime.now(UTC)},
    )
    return run_id


def content_hash(records: list[CleanRecord]) -> str:
    """Stable, order-independent hash of *this run's* loaded batch.

    SPEC.md §3 describes `pipeline_runs.content_hash` as a "hash of the
    loaded dataset". This hashes the `CleanRecord`s written by this run
    (i.e. the batch passed to `load_batch`) — not a snapshot of the full
    `medications` table afterwards. For a full feed (SPEC.md's `feed_v1.csv`)
    those are the same set of rows, so re-running the same full feed twice
    yields the same `content_hash` (idempotency is verified this way in
    `tests/test_pipeline.py`). For a delta feed (`feed_v2_delta.csv`), the
    hash reflects only the delta actually loaded in that run, not the whole
    table — deliberate: it identifies *what this run loaded*, which is what
    a run manifest/lineage record should attest to, rather than requiring an
    extra full-table read on every run just to compute a hash.
    """
    digest = hashlib.sha256()
    for r in sorted(records, key=lambda r: r.pzn):
        digest.update(
            "|".join(
                [
                    r.pzn, r.name, r.active_ingredient, r.dosage_form, r.strength,
                    str(r.prescription_only), str(r.price), str(r.manufacturer_id),
                    r.atc_code or "",
                ]
            ).encode("utf-8")
        )
    return digest.hexdigest()


def finish_run(
    session: Session,
    run_id: uuid.UUID,
    *,
    rows_in: int,
    rows_out: int,
    rows_rejected: int,
    status: str,
    content_hash_value: str | None,
) -> None:
    session.execute(
        text(
            "UPDATE pipeline_runs SET "
            "  rows_in = :rows_in, rows_out = :rows_out, rows_rejected = :rows_rejected, "
            "  finished_at = :finished_at, status = :status, content_hash = :content_hash "
            "WHERE run_id = :run_id"
        ),
        {
            "rows_in": rows_in,
            "rows_out": rows_out,
            "rows_rejected": rows_rejected,
            "finished_at": datetime.now(UTC),
            "status": status,
            "content_hash": content_hash_value,
            "run_id": run_id,
        },
    )
