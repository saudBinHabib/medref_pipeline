"""CLI entrypoint: orchestrates ingest -> transform -> enrich -> load (SPEC.md §5.7)."""

import argparse
import csv
import logging
import os
import sys
import tempfile
from pathlib import Path

import boto3

from src.config import get_settings
from src.db import get_session_factory
from src.enrich import ensure_atc_reference_rows, load_atc_reference_csv, resolve_atc_codes
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

    This is the single dead-letter decision point for unresolvable atc_codes
    (src/enrich.py's `resolve_atc_codes` only resolves codes; it never
    decides what happens to a row). `known_atc_codes` is the `resolved` dict
    (keys) from `resolve_atc_codes` — i.e. every code resolvable via the
    offline CSV *and*, if `ATC_REMOTE_LOOKUP` is on, the remote fallback. A
    row with no atc_code (nullable FK) is always resolvable. A row whose
    atc_code is well-formed but resolvable by no source would violate the
    medications.atc_code FK constraint and abort the whole batch if loaded
    (SPEC.md §5.1/§7 require dead-lettering instead).
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


def _parse_s3_uri(uri: str) -> tuple[str, str]:
    """Split an `s3://bucket/key` URI into (bucket, key)."""
    without_scheme = uri.removeprefix("s3://")
    bucket, _, key = without_scheme.partition("/")
    return bucket, key


def _download_feed_from_s3(uri: str) -> Path:
    """Download an `s3://bucket/key` feed object to a local temp file, return its path."""
    bucket, key = _parse_s3_uri(uri)
    suffix = Path(key).suffix or ".csv"
    fd, tmp_name = tempfile.mkstemp(suffix=suffix)
    os.close(fd)
    local_path = Path(tmp_name)
    boto3.client("s3").download_file(bucket, key, str(local_path))
    logger.info("downloaded feed from s3 bucket=%s key=%s to=%s", bucket, key, local_path)
    return local_path


def _upload_dead_letter_to_s3(dead_letter_path: Path, run_id: str) -> None:
    """Best-effort upload of the run's dead-letter CSV to the configured S3 bucket.

    Mirrors this codebase's dead-letter-not-abort philosophy: a failure here is
    logged but must never change the pipeline's own success/failure result.
    """
    bucket = get_settings().dead_letter_bucket
    if not bucket:
        return
    try:
        key = f"{run_id}.csv"
        boto3.client("s3").upload_file(str(dead_letter_path), bucket, key)
        logger.info("uploaded dead-letter file to s3 bucket=%s key=%s", bucket, key)
    except Exception:
        logger.exception(
            "failed to upload dead-letter file to s3 run_id=%s bucket=%s", run_id, bucket
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
    dead_letter_path = dead_letter_dir / f"{run_id}.csv"

    try:
        valid_rows = list(ingest_feed(feed_path, dead_letter_path))
        ingest_rejected = count_dead_letter_rows(dead_letter_path)
        rows_in = len(valid_rows) + ingest_rejected
        logger.info(
            "ingest complete run_id=%s rows_in=%d rows_rejected=%d",
            run_id, rows_in, ingest_rejected,
        )

        deduped = deduplicate(valid_rows)

        # ATC resolution: data/atc_reference.csv is read exactly once per run
        # (single source of truth — see src/enrich.py::resolve_atc_codes) and
        # combined with a remote fallback when ATC_REMOTE_LOOKUP is set. A row
        # can be schema-valid (well-formed atc_code) yet reference a code no
        # source can resolve. Loading it would violate the medications.atc_code
        # FK and abort the whole batch, so reject it to dead-letter here
        # instead (SPEC.md §5.1/§7): the run must continue.
        atc_reference_descriptions = load_atc_reference_csv()
        feed_atc_codes = {r.atc_code for r in deduped if r.atc_code}
        resolved_atc, unresolved_atc_codes = resolve_atc_codes(
            feed_atc_codes,
            atc_reference_descriptions,
            remote=get_settings().atc_remote_lookup,
        )
        resolvable_rows, unknown_atc_rows = _split_unknown_atc_rows(deduped, set(resolved_atc))
        if unknown_atc_rows:
            _append_unknown_atc_rows_to_dead_letter(dead_letter_path, unknown_atc_rows)
            logger.info(
                "rejected %d row(s) with unresolvable atc_code run_id=%s",
                len(unknown_atc_rows), run_id,
            )
        if unresolved_atc_codes:
            logger.info(
                "unresolved atc_code(s) run_id=%s codes=%s",
                run_id, sorted(unresolved_atc_codes),
            )

        # Recompute after any dead-letter appends above so it reflects the final tally.
        rows_rejected = count_dead_letter_rows(dead_letter_path)

        clean_records = normalize(session, resolvable_rows)

        ensure_atc_reference_rows(session, resolved_atc)

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
        _upload_dead_letter_to_s3(dead_letter_path, run_id)
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
        _upload_dead_letter_to_s3(dead_letter_path, run_id)
        return 1
    finally:
        session.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the medref batch pipeline.")
    parser.add_argument(
        "--feed", required=True, type=str, help="Path to the feed CSV, or an s3://bucket/key URI"
    )
    args = parser.parse_args(argv)

    if args.feed.startswith("s3://"):
        feed_path = _download_feed_from_s3(args.feed)
    else:
        feed_path = Path(args.feed)

    return run_pipeline(feed_path)


if __name__ == "__main__":
    sys.exit(main())
