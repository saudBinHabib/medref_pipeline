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
    """Stable, order-independent hash of the loaded batch."""
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
