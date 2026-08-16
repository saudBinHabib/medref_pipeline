"""SPEC.md §9 — valid rows pass, invalid rows land in dead-letter with reasons."""

import csv

from src.ingest import count_dead_letter_rows, ingest_feed

FEED_HEADER = [
    "pzn", "name", "active_ingredient", "dosage_form", "strength",
    "prescription_only", "price", "manufacturer", "atc_code",
]

VALID_ROW = [
    "12345678", "Testodol", "Paracetamol", "tablet", "500mg",
    "false", "9.99", "Nordhealth Pharma", "N02BE01",
]

INVALID_ROW_BAD_PZN = [
    "ABCDEFGH", "Testodol", "Paracetamol", "tablet", "500mg",
    "false", "9.99", "Nordhealth Pharma", "N02BE01",
]

INVALID_ROW_BAD_DOSAGE_FORM = [
    "87654321", "Testodol", "Paracetamol", "lozenge", "500mg",
    "false", "9.99", "Nordhealth Pharma", "N02BE01",
]


def _write_feed(path, rows):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(FEED_HEADER)
        writer.writerows(rows)


def test_valid_rows_are_yielded(tmp_path):
    feed_path = tmp_path / "feed.csv"
    _write_feed(feed_path, [VALID_ROW])
    dead_letter_path = tmp_path / "dead_letter.csv"

    rows = list(ingest_feed(feed_path, dead_letter_path))

    assert len(rows) == 1
    assert rows[0].pzn == "12345678"


def test_invalid_rows_land_in_dead_letter_with_reason(tmp_path):
    feed_path = tmp_path / "feed.csv"
    _write_feed(feed_path, [INVALID_ROW_BAD_PZN])
    dead_letter_path = tmp_path / "dead_letter.csv"

    rows = list(ingest_feed(feed_path, dead_letter_path))

    assert rows == []
    with open(dead_letter_path, newline="", encoding="utf-8") as fh:
        dead_rows = list(csv.DictReader(fh))
    assert len(dead_rows) == 1
    assert "pzn" in dead_rows[0]["rejection_reason"]


def test_ingestion_does_not_abort_on_bad_rows(tmp_path):
    feed_path = tmp_path / "feed.csv"
    _write_feed(feed_path, [INVALID_ROW_BAD_PZN, VALID_ROW, INVALID_ROW_BAD_DOSAGE_FORM])
    dead_letter_path = tmp_path / "dead_letter.csv"

    rows = list(ingest_feed(feed_path, dead_letter_path))

    assert len(rows) == 1
    assert rows[0].pzn == "12345678"
    assert count_dead_letter_rows(dead_letter_path) == 2
