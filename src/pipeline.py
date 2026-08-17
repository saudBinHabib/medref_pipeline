"""CLI entrypoint: orchestrates ingest -> transform -> enrich -> load (SPEC.md §5.7)."""

import argparse
import logging
import sys
from pathlib import Path

from src.db import get_session_factory
from src.enrich import ensure_atc_reference_rows
from src.ingest import count_dead_letter_rows, ingest_feed
from src.lineage import content_hash, finish_run, start_run
from src.load import load_batch
from src.transform import deduplicate, normalize

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger(__name__)


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
        rows_rejected = count_dead_letter_rows(dead_letter_path)
        rows_in = len(valid_rows) + rows_rejected
        logger.info(
            "ingest complete run_id=%s rows_in=%d rows_rejected=%d", run_id, rows_in, rows_rejected
        )

        deduped = deduplicate(valid_rows)
        clean_records = normalize(session, deduped)

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
