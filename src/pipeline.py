"""CLI entrypoint: orchestrates ingest -> transform -> enrich -> load (SPEC.md §5.7)."""

import argparse
import csv
import logging
import sys
from pathlib import Path

from src.db import get_session_factory
from src.enrich import ensure_atc_reference_rows, load_atc_reference_csv
from src.ingest import DEAD_LETTER_FIELDNAMES, count_dead_letter_rows, ingest_feed
from src.lineage import content_hash, finish_run, start_run
from src.load import load_batch
from src.schema import FeedRow
from src.transform import deduplicate, normalize

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger(__name__)


def _split_unknown_atc_rows(
    rows: list[FeedRow], known_atc_codes: set[str]
) -> tuple[list[FeedRow], list[FeedRow]]:
    """Partition rows into (resolvable, unresolvable) by atc_code.

    A row with no atc_code (nullable FK) is always resolvable. A row whose
    atc_code is well-formed but absent from data/atc_reference.csv is not —
    loading it would violate the medications.atc_code FK constraint and abort
    the whole batch (SPEC.md §5.1/§7 require dead-lettering instead).
    """
    resolvable: list[FeedRow] = []
    unresolvable: list[FeedRow] = []
    for row in rows:
        if row.atc_code is not None and row.atc_code not in known_atc_codes:
            unresolvable.append(row)
        else:
            resolvable.append(row)
    return resolvable, unresolvable


def _append_unknown_atc_rows_to_dead_letter(
    dead_letter_path: Path, rows: list[FeedRow]
) -> None:
    """Append rows rejected for an unresolvable atc_code to the run's dead-letter file.

    `ingest_feed` already created and closed this file (with a header) by the
    time this runs, so we open in append mode and reuse the same column shape.
    """
    if not rows:
        return
    with open(dead_letter_path, "a", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=DEAD_LETTER_FIELDNAMES)
        for row in rows:
            writer.writerow(
                {
                    "pzn": row.pzn,
                    "name": row.name,
                    "active_ingredient": row.active_ingredient,
                    "dosage_form": row.dosage_form,
                    "strength": row.strength,
                    "prescription_only": row.prescription_only,
                    "price": row.price,
                    "manufacturer": row.manufacturer,
                    "atc_code": row.atc_code,
                    "rejection_reason": f"atc_code: unknown code '{row.atc_code}'",
                }
            )


def run_pipeline(feed_path: Path, dead_letter_dir: Path = Path("dead_letter")) -> int:
    """Run one pipeline pass over `feed_path`. Returns 0 on success, 1 on failure."""
    session = get_session_factory()()
    try:
        run_id = start_run(session, source_file=str(feed_path))
        session.commit()  # run row is visible even if the rest of the run fails
    except Exception:
        logger.exception("pipeline failed to initialize run")
        session.close()
        return 1

    rows_in = 0
    rows_out = 0
    rows_rejected = 0

    try:
        dead_letter_path = dead_letter_dir / f"{run_id}.csv"
        valid_rows = list(ingest_feed(feed_path, dead_letter_path))
        ingest_rejected = count_dead_letter_rows(dead_letter_path)
        rows_in = len(valid_rows) + ingest_rejected
        logger.info(
            "ingest complete run_id=%s rows_in=%d rows_rejected=%d",
            run_id, rows_in, ingest_rejected,
        )

        deduped = deduplicate(valid_rows)

        # A row can be schema-valid (well-formed atc_code) yet reference a code
        # absent from data/atc_reference.csv. Loading it would violate the
        # medications.atc_code FK and abort the whole batch, so reject it to
        # dead-letter here instead (SPEC.md §5.1/§7): the run must continue.
        known_atc_codes = set(load_atc_reference_csv().keys())
        resolvable_rows, unknown_atc_rows = _split_unknown_atc_rows(deduped, known_atc_codes)
        if unknown_atc_rows:
            _append_unknown_atc_rows_to_dead_letter(dead_letter_path, unknown_atc_rows)
            logger.info(
                "rejected %d row(s) with unresolvable atc_code run_id=%s",
                len(unknown_atc_rows), run_id,
            )

        # Recompute after any dead-letter appends above so it reflects the final tally.
        rows_rejected = count_dead_letter_rows(dead_letter_path)

        clean_records = normalize(session, resolvable_rows)

        atc_codes = {r.atc_code for r in clean_records if r.atc_code}
        ensure_atc_reference_rows(session, atc_codes)

        load_batch(session, clean_records)
        rows_out = len(clean_records)
        logger.info("load complete run_id=%s rows_out=%d", run_id, rows_out)

        hash_value = content_hash(clean_records)
        finish_run(
            session, run_id,
            rows_in=rows_in, rows_out=rows_out, rows_rejected=rows_rejected,
            status="success", content_hash_value=hash_value,
        )
        session.commit()
        logger.info("pipeline succeeded run_id=%s", run_id)
        return 0
    except Exception:
        session.rollback()
        logger.exception("pipeline failed run_id=%s", run_id)
        failure_session = get_session_factory()()
        try:
            finish_run(
                failure_session, run_id,
                rows_in=rows_in, rows_out=rows_out, rows_rejected=rows_rejected,
                status="failed", content_hash_value=None,
            )
            failure_session.commit()
        finally:
            failure_session.close()
        return 1
    finally:
        session.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the medref batch pipeline.")
    parser.add_argument("--feed", required=True, type=Path, help="Path to the feed CSV")
    args = parser.parse_args(argv)
    return run_pipeline(args.feed)


if __name__ == "__main__":
    sys.exit(main())
