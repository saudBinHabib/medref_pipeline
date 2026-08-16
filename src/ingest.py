"""Streaming ingestion + dead-letter (SPEC.md §5.1)."""

import csv
from collections.abc import Iterator
from pathlib import Path

from pydantic import ValidationError

from src.schema import FeedRow

DEAD_LETTER_FIELDNAMES = [
    "pzn", "name", "active_ingredient", "dosage_form", "strength",
    "prescription_only", "price", "manufacturer", "atc_code", "rejection_reason",
]


def _rejection_reason(exc: ValidationError) -> str:
    return "; ".join(f"{err['loc'][0]}: {err['msg']}" for err in exc.errors())


def ingest_feed(feed_path: Path, dead_letter_path: Path) -> Iterator[FeedRow]:
    """Stream-validate a feed CSV. Valid rows are yielded; invalid rows are
    written to `dead_letter_path` with an added `rejection_reason` column.
    Never aborts on a single bad row.
    """
    dead_letter_path.parent.mkdir(parents=True, exist_ok=True)
    with (
        open(feed_path, newline="", encoding="utf-8") as feed_fh,
        open(dead_letter_path, "w", newline="", encoding="utf-8") as dl_fh,
    ):
        reader = csv.DictReader(feed_fh)
        writer = csv.DictWriter(dl_fh, fieldnames=DEAD_LETTER_FIELDNAMES)
        writer.writeheader()
        for raw_row in reader:
            try:
                yield FeedRow.model_validate(raw_row)
            except ValidationError as exc:
                writer.writerow({**raw_row, "rejection_reason": _rejection_reason(exc)})


def count_dead_letter_rows(dead_letter_path: Path) -> int:
    with open(dead_letter_path, newline="", encoding="utf-8") as fh:
        return sum(1 for _ in csv.DictReader(fh))
