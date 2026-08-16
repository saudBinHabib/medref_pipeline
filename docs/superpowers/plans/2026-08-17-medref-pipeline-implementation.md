# medref-pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the full batch drug-reference pipeline (ingest → transform → enrich → load, with lineage) and the versioned FastAPI serving layer described in SPEC.md, on top of the already-scaffolded repo (pyproject.toml, docker-compose.yml, migrations/001_init.sql, src/config.py, src/db.py).

**Architecture:** Each pipeline stage is a pure, independently-callable function operating on Pydantic models / plain dataclasses; `src/pipeline.py` composes them and owns the single database transaction for a run (commit on success, rollback + failure-lineage record on error). The API is a thin read layer over the `medications` serving table using parameterized SQLAlchemy Core queries — no ORM mapping needed since there's no write path from the API.

**Tech Stack:** Python 3.11+, FastAPI, Pydantic v2, PostgreSQL 16, SQLAlchemy 2.x Core (parameterized `text()` queries via psycopg 3), pytest + FastAPI `TestClient`, ruff, Docker/docker-compose, GitHub Actions.

**Spec:** `SPEC.md` (repo root) — this plan implements §4–§11 (build order steps 2–11 in §13; step 1 was already scaffolded).

## Global Constraints

- Python 3.11+; Pydantic v2; SQLAlchemy 2.x; PostgreSQL 16. (SPEC.md §2)
- **Parameterized SQL only** — every query uses SQLAlchemy `text()` with bound `:param` placeholders, never string interpolation. (SPEC.md §7)
- No cloud dependencies to run locally; object storage is a local directory (`dead_letter/`). (SPEC.md §2)
- Batch only, triggered manually via CLI — no streaming, no Spark. (SPEC.md §2)
- Config via environment variables / `.env`, never hardcoded (already implemented in `src/config.py`). (SPEC.md §7)
- `pzn` is always a `str`, exactly 8 digits, leading zeros preserved — never coerce to `int` anywhere in the pipeline or API. (SPEC.md §3, §5.2)
- **Design deviation from SPEC.md §2's "Polars (preferred)":** this plan uses the stdlib `csv` module for streaming ingestion instead of Polars. Rationale: Polars/pandas both risk silently inferring `pzn` as an integer column (stripping leading zeros) unless a dtype override is threaded through every read; `csv.DictReader` reads every field as `str` by default, which is the actually-required behavior, and the feed sizes (50–200 rows) have no performance case for a columnar engine. `polars` is dropped from `pyproject.toml` in Task 1 as an unused dependency (YAGNI). All other stages (dedup, enrich, load) operate on Pydantic models / dataclasses with plain Python + parameterized SQL.
- Tests that touch the database are integration tests against a real, reachable PostgreSQL 16 (via `DATABASE_URL`) — not mocked — because the spec's correctness requirements (idempotent upsert, atomic swap, FK/CHECK constraints) are properties of real Postgres behavior. `docker-compose up -d postgres` (or the CI service container set up in Task 9) must be running before `pytest`.

---

## Task 1: Trim dependencies + synthetic data generator

**Files:**
- Modify: `pyproject.toml`
- Create: `scripts/generate_data.py`
- Create: `data/feed_v1.csv`, `data/feed_v2_delta.csv`, `data/feed_broken.csv`, `data/atc_reference.csv` (generated output, committed)

**Interfaces:**
- Produces: four CSV fixtures under `data/` with the exact columns SPEC.md §4 requires: feed columns `pzn, name, active_ingredient, dosage_form, strength, prescription_only, price, manufacturer, atc_code`; `atc_reference.csv` columns `atc_code, atc_description`.
- Produces: `feed_v1.csv` has exactly 80 rows, all unique PZNs `00000001`–`00000080`.
- Produces: `feed_v2_delta.csv` has exactly 20 rows — 10 rows reusing existing PZNs from `feed_v1` with `name` suffixed `" Forte"` and a changed `price`, and 10 rows with brand-new PZNs `00000081`–`00000090`.
- Produces: `feed_broken.csv` has exactly 8 rows: one each violating missing-field, non-numeric-PZN, wrong-length-PZN, bad-dosage_form, non-boolean-prescription_only, negative-price, plus **two** rows sharing PZN `10000006` (duplicate-PZN case — each individually schema-valid; deduplication, not schema rejection, is what removes the extra one — see Task 4).

- [ ] **Step 1: Update `pyproject.toml`**

Remove the unused `polars` dependency (see Global Constraints) and keep everything else as scaffolded:

```toml
[project]
name = "medref-pipeline"
version = "0.1.0"
description = "Batch drug reference data pipeline and serving API"
requires-python = ">=3.11"
dependencies = [
    "fastapi>=0.115",
    "uvicorn[standard]>=0.30",
    "pydantic>=2.7",
    "pydantic-settings>=2.3",
    "sqlalchemy>=2.0",
    "psycopg[binary]>=3.1",
    "python-dotenv>=1.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "httpx>=0.27",
    "ruff>=0.5",
]

[build-system]
requires = ["setuptools>=68"]
build-backend = "setuptools.build_meta"

[tool.setuptools.packages.find]
include = ["src*"]

[tool.ruff]
line-length = 100
target-version = "py311"

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B"]

[tool.pytest.ini_options]
testpaths = ["tests"]
```

- [ ] **Step 2: Write the data generator**

Create `scripts/generate_data.py`:

```python
"""Generate synthetic feed + ATC reference CSVs under data/ (SPEC.md §4).

Run: python scripts/generate_data.py
"""

import csv
import random
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

# Real WHO ATC classification codes (public domain classification system,
# not licensed drug data) paired with their descriptions.
ATC_CODES = [
    ("N02BE01", "Paracetamol"),
    ("M01AE01", "Ibuprofen"),
    ("J01CA04", "Amoxicillin"),
    ("A10BA02", "Metformin"),
    ("A02BC01", "Omeprazole"),
    ("C10AA05", "Atorvastatin"),
    ("A10AB01", "Insulin (human)"),
    ("R03AC02", "Salbutamol"),
    ("N05BA01", "Diazepam"),
    ("R06AX13", "Loratadine"),
    ("C09AA02", "Enalapril"),
    ("N06AB03", "Fluoxetine"),
    ("J01FA01", "Erythromycin"),
    ("C07AB02", "Metoprolol"),
    ("B01AC06", "Acetylsalicylic acid"),
    ("M01AB05", "Diclofenac"),
    ("N02AA01", "Morphine"),
    ("A03FA01", "Metoclopramide"),
    ("N05BA06", "Lorazepam"),
    ("C08CA01", "Amlodipine"),
]

DOSAGE_FORMS = ["tablet", "capsule", "solution", "injection", "cream", "drops", "spray"]

MANUFACTURERS = [
    "Nordhealth Pharma", "Blauberg Labs", "Vitalis Biotech", "Greenfield Pharmaceuticals",
    "Sonnenberg Medica", "Rheinquell Pharma", "Alpenrose Labs", "Nordwind Biosciences",
]

BRAND_PREFIXES = ["Medo", "Curalin", "Biovex", "Pharmatec", "Sanavia", "Novapex", "Rekura", "Vitapharm"]

FEED_COLUMNS = [
    "pzn", "name", "active_ingredient", "dosage_form", "strength",
    "prescription_only", "price", "manufacturer", "atc_code",
]


def _strength_for(dosage_form: str, rng: random.Random) -> str:
    if dosage_form in ("tablet", "capsule"):
        return f"{rng.choice([100, 200, 250, 400, 500, 800])}mg"
    if dosage_form == "solution":
        return f"{rng.choice([5, 10, 20])}mg/ml"
    if dosage_form == "injection":
        return f"{rng.choice([1, 2, 5])}ml"
    if dosage_form == "cream":
        return f"{rng.choice([1, 2, 5])}%"
    if dosage_form == "drops":
        return f"{rng.choice([5, 10, 15])}ml"
    return f"{rng.choice([50, 100])}mcg"  # spray


def generate_rows(n: int, rng: random.Random, start_pzn: int = 1) -> list[dict]:
    rows = []
    for i in range(n):
        pzn = f"{start_pzn + i:08d}"
        atc_code, ingredient = rng.choice(ATC_CODES)
        dosage_form = rng.choice(DOSAGE_FORMS)
        rows.append(
            {
                "pzn": pzn,
                "name": f"{rng.choice(BRAND_PREFIXES)} {ingredient.split()[0]}",
                "active_ingredient": ingredient,
                "dosage_form": dosage_form,
                "strength": _strength_for(dosage_form, rng),
                "prescription_only": rng.choice(["true", "false"]),
                "price": f"{rng.uniform(2.5, 89.99):.2f}",
                "manufacturer": rng.choice(MANUFACTURERS),
                "atc_code": atc_code,
            }
        )
    return rows


def write_csv(path: Path, rows: list[dict], columns: list[str]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def build_feed_v1(rng: random.Random) -> list[dict]:
    return generate_rows(80, rng, start_pzn=1)


def build_feed_v2_delta(feed_v1: list[dict], rng: random.Random) -> list[dict]:
    """10 changed existing rows (price/name) + 10 brand-new PZNs."""
    changed = []
    for row in rng.sample(feed_v1, 10):
        updated = dict(row)
        updated["price"] = f"{rng.uniform(2.5, 89.99):.2f}"
        updated["name"] = updated["name"] + " Forte"
        changed.append(updated)
    new_rows = generate_rows(10, rng, start_pzn=81)
    return changed + new_rows


def build_feed_broken() -> list[dict]:
    base = {
        "pzn": "10000001", "name": "Testodol", "active_ingredient": "Paracetamol",
        "dosage_form": "tablet", "strength": "500mg", "prescription_only": "false",
        "price": "9.99", "manufacturer": "Nordhealth Pharma", "atc_code": "N02BE01",
    }

    def variant(**overrides):
        row = dict(base)
        row.update(overrides)
        return row

    return [
        variant(pzn="10000002", name=""),                    # missing required field
        variant(pzn="ABCDEFGH"),                              # non-numeric pzn
        variant(pzn="123"),                                   # wrong-length pzn
        variant(pzn="10000003", dosage_form="lozenge"),       # dosage_form outside allowed set
        variant(pzn="10000004", prescription_only="maybe"),   # non-boolean prescription_only
        variant(pzn="10000005", price="-5.00"),                # negative price
        variant(pzn="10000006"),                               # duplicate pzn (1 of 2)
        variant(pzn="10000006"),                               # duplicate pzn (2 of 2)
    ]


def main() -> None:
    rng = random.Random(42)
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    feed_v1 = build_feed_v1(rng)
    write_csv(DATA_DIR / "feed_v1.csv", feed_v1, FEED_COLUMNS)

    feed_v2_delta = build_feed_v2_delta(feed_v1, rng)
    write_csv(DATA_DIR / "feed_v2_delta.csv", feed_v2_delta, FEED_COLUMNS)

    feed_broken = build_feed_broken()
    write_csv(DATA_DIR / "feed_broken.csv", feed_broken, FEED_COLUMNS)

    write_csv(
        DATA_DIR / "atc_reference.csv",
        [{"atc_code": code, "atc_description": desc} for code, desc in ATC_CODES],
        ["atc_code", "atc_description"],
    )
    print(f"Wrote {len(feed_v1)} + {len(feed_v2_delta)} + {len(feed_broken)} feed rows "
          f"and {len(ATC_CODES)} ATC reference rows to {DATA_DIR}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Run the generator and verify row counts**

```bash
python scripts/generate_data.py
wc -l data/feed_v1.csv data/feed_v2_delta.csv data/feed_broken.csv data/atc_reference.csv
```

Expected: `feed_v1.csv` 81 lines (80 + header), `feed_v2_delta.csv` 21 lines, `feed_broken.csv` 9 lines, `atc_reference.csv` 21 lines.

- [ ] **Step 4: Commit**

```bash
git add pyproject.toml scripts/generate_data.py data/feed_v1.csv data/feed_v2_delta.csv data/feed_broken.csv data/atc_reference.csv
git commit -m "feat: add synthetic data generator and commit generated fixtures"
```

---

## Task 2: Schema contract (`src/schema.py`)

**Files:**
- Create: `src/schema.py`
- Test: `tests/test_schema.py`

**Interfaces:**
- Produces: `FeedRow` (Pydantic model) — fields `pzn: str, name: str, active_ingredient: str, dosage_form: str, strength: str, prescription_only: bool, price: Decimal, manufacturer: str, atc_code: str | None`.
- Produces: `Medication` (Pydantic model) — fields `pzn: str, name: str, active_ingredient: str, dosage_form: str, strength: str, prescription_only: bool, price: Decimal, manufacturer_id: int, atc_code: str | None`.
- Produces: `ALLOWED_DOSAGE_FORMS: set[str]` constant, reused by the API in Task 8.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_schema.py`:

```python
"""SPEC.md §9 — every FeedRow validation rule, valid + each invalid case."""

from decimal import Decimal

import pytest
from pydantic import ValidationError

from src.schema import FeedRow

VALID_ROW = {
    "pzn": "12345678",
    "name": "Testodol",
    "active_ingredient": "Paracetamol",
    "dosage_form": "tablet",
    "strength": "500mg",
    "prescription_only": "false",
    "price": "9.99",
    "manufacturer": "Nordhealth Pharma",
    "atc_code": "N02BE01",
}


def test_valid_row_parses():
    row = FeedRow.model_validate(VALID_ROW)
    assert row.pzn == "12345678"
    assert row.prescription_only is False
    assert row.price == Decimal("9.99")
    assert row.atc_code == "N02BE01"


def test_pzn_preserves_leading_zeros():
    row = FeedRow.model_validate({**VALID_ROW, "pzn": "00012345"})
    assert row.pzn == "00012345"


@pytest.mark.parametrize(
    "overrides,expected_fragment",
    [
        ({"name": ""}, "name"),
        ({"pzn": "ABCDEFGH"}, "pzn"),
        ({"pzn": "123"}, "pzn"),
        ({"dosage_form": "lozenge"}, "dosage_form"),
        ({"prescription_only": "maybe"}, "prescription_only"),
        ({"price": "-5.00"}, "price"),
        ({"atc_code": "not-a-code"}, "atc_code"),
    ],
)
def test_invalid_rows_rejected_with_reason(overrides, expected_fragment):
    with pytest.raises(ValidationError) as exc_info:
        FeedRow.model_validate({**VALID_ROW, **overrides})
    assert expected_fragment in str(exc_info.value)


@pytest.mark.parametrize(
    "value,expected",
    [
        ("true", True), ("1", True), ("yes", True), ("YES", True),
        ("false", False), ("0", False), ("no", False), ("NO", False),
    ],
)
def test_prescription_only_accepts_boolean_like_values(value, expected):
    row = FeedRow.model_validate({**VALID_ROW, "prescription_only": value})
    assert row.prescription_only is expected


def test_atc_code_optional():
    row = FeedRow.model_validate({**VALID_ROW, "atc_code": ""})
    assert row.atc_code is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_schema.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.schema'`

- [ ] **Step 3: Implement `src/schema.py`**

```python
"""Pydantic v2 schema contracts for feed rows and served medications (SPEC.md §5.2)."""

import re
from decimal import Decimal, InvalidOperation

from pydantic import BaseModel, ConfigDict, field_validator

ALLOWED_DOSAGE_FORMS = {"tablet", "capsule", "solution", "injection", "cream", "drops", "spray"}
ATC_CODE_PATTERN = re.compile(r"^[A-Z]\d{2}[A-Z]{2}\d{2}$")
_TRUE_VALUES = {"true", "1", "yes"}
_FALSE_VALUES = {"false", "0", "no"}


class FeedRow(BaseModel):
    """Raw incoming feed row, validated per SPEC.md §5.2."""

    model_config = ConfigDict(str_strip_whitespace=True)

    pzn: str
    name: str
    active_ingredient: str
    dosage_form: str
    strength: str
    prescription_only: bool
    price: Decimal
    manufacturer: str
    atc_code: str | None = None

    @field_validator("pzn")
    @classmethod
    def validate_pzn(cls, v: str) -> str:
        if not v.isdigit() or len(v) != 8:
            raise ValueError(f"pzn must be exactly 8 numeric characters, got {v!r}")
        return v

    @field_validator("name", "active_ingredient", "strength", "manufacturer")
    @classmethod
    def validate_non_empty(cls, v: str, info) -> str:
        if not v:
            raise ValueError(f"{info.field_name} must not be empty")
        return v

    @field_validator("dosage_form")
    @classmethod
    def validate_dosage_form(cls, v: str) -> str:
        if v not in ALLOWED_DOSAGE_FORMS:
            raise ValueError(
                f"dosage_form must be one of {sorted(ALLOWED_DOSAGE_FORMS)}, got {v!r}"
            )
        return v

    @field_validator("prescription_only", mode="before")
    @classmethod
    def validate_prescription_only(cls, v: object) -> bool:
        if isinstance(v, bool):
            return v
        s = str(v).strip().lower()
        if s in _TRUE_VALUES:
            return True
        if s in _FALSE_VALUES:
            return False
        raise ValueError(
            f"prescription_only must be a boolean-like value (true/false/1/0/yes/no), got {v!r}"
        )

    @field_validator("price", mode="before")
    @classmethod
    def validate_price(cls, v: object) -> Decimal:
        try:
            price = Decimal(str(v))
        except InvalidOperation as exc:
            raise ValueError(f"price must be a valid decimal number, got {v!r}") from exc
        if price < 0:
            raise ValueError(f"price must be >= 0, got {price}")
        return price

    @field_validator("atc_code")
    @classmethod
    def validate_atc_code(cls, v: str | None) -> str | None:
        if v is None or v == "":
            return None
        if not ATC_CODE_PATTERN.match(v):
            raise ValueError(f"atc_code must match ATC format (e.g. 'M01AE01'), got {v!r}")
        return v


class Medication(BaseModel):
    """Clean/serving model returned by the API (SPEC.md §5.2, §6)."""

    model_config = ConfigDict(from_attributes=True)

    pzn: str
    name: str
    active_ingredient: str
    dosage_form: str
    strength: str
    prescription_only: bool
    price: Decimal
    manufacturer_id: int
    atc_code: str | None = None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_schema.py -v`
Expected: PASS (all cases)

- [ ] **Step 5: Lint and commit**

```bash
ruff check src/schema.py tests/test_schema.py
git add src/schema.py tests/test_schema.py
git commit -m "feat: add FeedRow/Medication schema contracts"
```

---

## Task 3: Streaming ingestion + dead-letter (`src/ingest.py`)

**Files:**
- Create: `src/ingest.py`
- Test: `tests/test_ingest.py`

**Interfaces:**
- Consumes: `FeedRow` from `src.schema` (Task 2).
- Produces: `ingest_feed(feed_path: Path, dead_letter_path: Path) -> Iterator[FeedRow]` — generator; valid rows yielded, invalid rows written to `dead_letter_path` with a `rejection_reason` column.
- Produces: `count_dead_letter_rows(dead_letter_path: Path) -> int` — used by `src/pipeline.py` (Task 7) to compute `rows_rejected`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_ingest.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_ingest.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.ingest'`

- [ ] **Step 3: Implement `src/ingest.py`**

```python
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_ingest.py -v`
Expected: PASS

- [ ] **Step 5: Lint and commit**

```bash
ruff check src/ingest.py tests/test_ingest.py
git add src/ingest.py tests/test_ingest.py
git commit -m "feat: add streaming ingestion with dead-letter routing"
```

---

## Task 4: Shared DB test fixtures + transform (`src/transform.py`)

**Files:**
- Create: `tests/conftest.py`
- Create: `src/transform.py`
- Test: `tests/test_transform.py`

**Interfaces:**
- Consumes: `FeedRow` from `src.schema` (Task 2); `Session`/`get_engine`/`get_session_factory` from `src.db` (already scaffolded).
- Produces: `tests/conftest.py` fixtures `db_session` and `clean_db`, reused by every remaining DB-touching test file (Tasks 4, 5, 6, 7, 8).
- Produces: `CleanRecord` (dataclass) — fields `pzn, name, active_ingredient, dosage_form, strength, prescription_only, price, manufacturer_id, atc_code` — consumed by `src/load.py` (Task 6) and `src/lineage.py`/`src/pipeline.py` (Task 7).
- Produces: `deduplicate(rows: list[FeedRow]) -> list[FeedRow]`.
- Produces: `resolve_manufacturer_id(session: Session, name: str) -> int`.
- Produces: `normalize(session: Session, rows: list[FeedRow]) -> list[CleanRecord]`.

- [ ] **Step 1: Write the shared DB fixtures**

Create `tests/conftest.py`:

```python
"""Shared pytest fixtures. Requires a reachable PostgreSQL 16 (see README)."""

from pathlib import Path

import pytest
from sqlalchemy import text

from src.db import get_engine, get_session_factory

MIGRATION_SQL_PATH = Path(__file__).resolve().parent.parent / "migrations" / "001_init.sql"

# FK-safe truncation order: children before parents.
TABLES_IN_TRUNCATE_ORDER = (
    "medications_staging",
    "medications",
    "pipeline_runs",
    "atc_reference",
    "manufacturers",
)


@pytest.fixture(scope="session", autouse=True)
def _apply_migrations():
    """Apply migrations/001_init.sql once per test session (idempotent DDL)."""
    raw_sql = MIGRATION_SQL_PATH.read_text()
    sql_lines = [line for line in raw_sql.splitlines() if not line.strip().startswith("--")]
    statements = [s.strip() for s in "\n".join(sql_lines).split(";") if s.strip()]

    engine = get_engine()
    with engine.begin() as conn:
        for statement in statements:
            conn.exec_driver_sql(statement)


@pytest.fixture
def db_session():
    session = get_session_factory()()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture
def clean_db(db_session):
    """Truncate all pipeline tables before the test runs."""
    for table in TABLES_IN_TRUNCATE_ORDER:
        db_session.execute(text(f"TRUNCATE TABLE {table} RESTART IDENTITY CASCADE"))
    db_session.commit()
    yield db_session
```

- [ ] **Step 2: Write the failing tests**

Create `tests/test_transform.py`:

```python
"""SPEC.md §9 — dedup keeps the correct record; manufacturer normalization is stable."""

from decimal import Decimal

from src.schema import FeedRow
from src.transform import deduplicate, normalize, resolve_manufacturer_id


def _row(pzn, name, manufacturer="Nordhealth Pharma"):
    return FeedRow.model_validate({
        "pzn": pzn, "name": name, "active_ingredient": "Paracetamol",
        "dosage_form": "tablet", "strength": "500mg", "prescription_only": "false",
        "price": "9.99", "manufacturer": manufacturer, "atc_code": "N02BE01",
    })


def test_deduplicate_keeps_last_occurrence():
    rows = [_row("11111111", "First"), _row("22222222", "Other"), _row("11111111", "Last")]

    result = deduplicate(rows)

    assert len(result) == 2
    by_pzn = {r.pzn: r for r in result}
    assert by_pzn["11111111"].name == "Last"


def test_deduplicate_preserves_first_seen_order():
    rows = [_row("22222222", "A"), _row("11111111", "B"), _row("22222222", "C")]

    result = deduplicate(rows)

    assert [r.pzn for r in result] == ["22222222", "11111111"]


def test_resolve_manufacturer_id_is_stable(clean_db):
    first = resolve_manufacturer_id(clean_db, "Nordhealth Pharma")
    second = resolve_manufacturer_id(clean_db, "Nordhealth Pharma")
    assert first == second


def test_resolve_manufacturer_id_inserts_new(clean_db):
    manufacturer_id = resolve_manufacturer_id(clean_db, "Brand New Labs")
    assert isinstance(manufacturer_id, int)


def test_normalize_resolves_manufacturer_ids(clean_db):
    rows = [
        _row("11111111", "First", "Nordhealth Pharma"),
        _row("22222222", "Second", "Nordhealth Pharma"),
    ]

    records = normalize(clean_db, rows)

    assert records[0].manufacturer_id == records[1].manufacturer_id
    assert records[0].price == Decimal("9.99")
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `pytest tests/test_transform.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.transform'`

- [ ] **Step 4: Implement `src/transform.py`**

```python
"""Deduplicate + normalize manufacturer names (SPEC.md §5.3)."""

from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.orm import Session

from src.schema import FeedRow


@dataclass
class CleanRecord:
    pzn: str
    name: str
    active_ingredient: str
    dosage_form: str
    strength: str
    prescription_only: bool
    price: Decimal
    manufacturer_id: int
    atc_code: str | None


def deduplicate(rows: list[FeedRow]) -> list[FeedRow]:
    """Keep the last occurrence of each pzn; preserve first-seen order otherwise."""
    last_by_pzn: dict[str, FeedRow] = {}
    order: list[str] = []
    for row in rows:
        if row.pzn not in last_by_pzn:
            order.append(row.pzn)
        last_by_pzn[row.pzn] = row
    return [last_by_pzn[pzn] for pzn in order]


def resolve_manufacturer_id(session: Session, name: str) -> int:
    """Look up manufacturer by name, inserting if new. Returns manufacturer_id."""
    existing = session.execute(
        text("SELECT manufacturer_id FROM manufacturers WHERE name = :name"),
        {"name": name},
    ).first()
    if existing is not None:
        return existing[0]
    inserted = session.execute(
        text(
            "INSERT INTO manufacturers (name) VALUES (:name) "
            "ON CONFLICT (name) DO UPDATE SET name = EXCLUDED.name "
            "RETURNING manufacturer_id"
        ),
        {"name": name},
    ).first()
    return inserted[0]


def normalize(session: Session, rows: list[FeedRow]) -> list[CleanRecord]:
    """Resolve manufacturer_id for each row. Call after deduplicate()."""
    return [
        CleanRecord(
            pzn=row.pzn,
            name=row.name,
            active_ingredient=row.active_ingredient,
            dosage_form=row.dosage_form,
            strength=row.strength,
            prescription_only=row.prescription_only,
            price=row.price,
            manufacturer_id=resolve_manufacturer_id(session, row.manufacturer),
            atc_code=row.atc_code,
        )
        for row in rows
    ]
```

- [ ] **Step 5: Run tests to verify they pass**

Ensure Postgres is reachable first: `docker-compose up -d postgres` (wait for healthy), then:

Run: `pytest tests/test_transform.py -v`
Expected: PASS

- [ ] **Step 6: Lint and commit**

```bash
ruff check src/transform.py tests/test_transform.py tests/conftest.py
git add tests/conftest.py src/transform.py tests/test_transform.py
git commit -m "feat: add dedup/normalize transform stage and shared DB test fixtures"
```

---

## Task 5: ATC enrichment (`src/enrich.py`)

**Files:**
- Create: `src/enrich.py`
- Test: `tests/test_enrich.py`

**Interfaces:**
- Consumes: `clean_db` fixture from `tests/conftest.py` (Task 4).
- Produces: `lookup_atc(code: str, *, remote: bool = False, fetch_fn=None, sleep_fn=time.sleep, max_retries=MAX_RETRIES) -> str | None`.
- Produces: `RetryableAtcError(retry_after: float | None = None)` — raised by a remote `fetch_fn` to request a retry.
- Produces: `AtcLookupExhausted` — raised when retries are exhausted.
- Produces: `ensure_atc_reference_rows(session: Session, codes: set[str]) -> None` — consumed by `src/pipeline.py` (Task 7).
- Produces: `load_atc_reference_csv(path=ATC_REFERENCE_CSV) -> dict[str, str]`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_enrich.py`:

```python
"""enrich.py — offline ATC lookup, remote retry with backoff + Retry-After (SPEC.md §5.4)."""

import pytest
from sqlalchemy import text

from src.enrich import (
    AtcLookupExhausted,
    RetryableAtcError,
    ensure_atc_reference_rows,
    lookup_atc,
)


def test_offline_lookup_reads_local_csv():
    assert lookup_atc("N02BE01", remote=False) == "Paracetamol"


def test_offline_lookup_unknown_code_returns_none():
    assert lookup_atc("Z99ZZ99", remote=False) is None


def test_remote_lookup_retries_then_succeeds():
    attempts = {"count": 0}

    def flaky_fetch(code):
        attempts["count"] += 1
        if attempts["count"] < 3:
            raise RetryableAtcError()
        return "Paracetamol"

    sleeps = []
    result = lookup_atc(
        "N02BE01", remote=True, fetch_fn=flaky_fetch, sleep_fn=sleeps.append, max_retries=5
    )

    assert result == "Paracetamol"
    assert attempts["count"] == 3
    assert len(sleeps) == 2


def test_remote_lookup_honors_retry_after():
    def fetch(code):
        raise RetryableAtcError(retry_after=2.5)

    sleeps = []
    with pytest.raises(AtcLookupExhausted):
        lookup_atc("N02BE01", remote=True, fetch_fn=fetch, sleep_fn=sleeps.append, max_retries=2)

    assert sleeps == [2.5, 2.5]


def test_remote_lookup_raises_after_max_retries():
    def always_fails(code):
        raise RetryableAtcError()

    with pytest.raises(AtcLookupExhausted):
        lookup_atc(
            "N02BE01", remote=True, fetch_fn=always_fails, sleep_fn=lambda _: None, max_retries=2
        )


def test_ensure_atc_reference_rows_upserts_missing_codes(clean_db):
    ensure_atc_reference_rows(clean_db, {"N02BE01", "M01AE01"})
    clean_db.commit()

    rows = clean_db.execute(text("SELECT atc_code FROM atc_reference ORDER BY atc_code")).all()
    assert [r[0] for r in rows] == ["M01AE01", "N02BE01"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_enrich.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.enrich'`

- [ ] **Step 3: Implement `src/enrich.py`**

```python
"""ATC enrichment + retry-wrapped remote lookup (SPEC.md §5.4)."""

from __future__ import annotations

import csv
import random
import time
from collections.abc import Callable
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.orm import Session

# Resolved relative to this module's file, not the process cwd, so it works
# regardless of where the pipeline/tests are invoked from.
ATC_REFERENCE_CSV = Path(__file__).resolve().parent.parent / "data" / "atc_reference.csv"
MAX_RETRIES = 5
BASE_BACKOFF_SECONDS = 0.5


class AtcLookupExhausted(Exception):
    """Raised when a remote ATC lookup exhausts its retry budget."""


class RetryableAtcError(Exception):
    """Raised by a remote fetch function to request a retry.

    `retry_after`, when set, overrides the exponential backoff delay
    (mirrors an HTTP `Retry-After` header).
    """

    def __init__(self, retry_after: float | None = None):
        self.retry_after = retry_after
        super().__init__(f"retryable ATC lookup error (retry_after={retry_after})")


def load_atc_reference_csv(path: Path = ATC_REFERENCE_CSV) -> dict[str, str]:
    with open(path, newline="", encoding="utf-8") as fh:
        return {row["atc_code"]: row["atc_description"] for row in csv.DictReader(fh)}


def lookup_atc(
    code: str,
    *,
    remote: bool = False,
    fetch_fn: Callable[[str], str | None] | None = None,
    sleep_fn: Callable[[float], None] = time.sleep,
    max_retries: int = MAX_RETRIES,
) -> str | None:
    """Return the ATC description for `code`, or None if unknown.

    Offline (`remote=False`, default): reads data/atc_reference.csv.
    Remote (`remote=True`): calls `fetch_fn(code)`, retrying on
    `RetryableAtcError` with exponential backoff + jitter (or the error's
    `retry_after`, if set) up to `max_retries` attempts.
    """
    if not remote:
        return load_atc_reference_csv().get(code)
    if fetch_fn is None:
        raise ValueError("fetch_fn is required when remote=True")

    attempt = 0
    while True:
        try:
            return fetch_fn(code)
        except RetryableAtcError as retry:
            attempt += 1
            if attempt > max_retries:
                raise AtcLookupExhausted(
                    f"exhausted {max_retries} retries looking up ATC code {code!r}"
                ) from retry
            delay = retry.retry_after
            if delay is None:
                delay = (BASE_BACKOFF_SECONDS * (2 ** (attempt - 1))) + random.uniform(
                    0, BASE_BACKOFF_SECONDS
                )
            sleep_fn(delay)


def ensure_atc_reference_rows(session: Session, codes: set[str]) -> None:
    """Upsert data/atc_reference.csv rows for every ATC code referenced in a feed.

    Called before load so medications.atc_code FK resolves.
    """
    if not codes:
        return
    descriptions = load_atc_reference_csv()
    for code in sorted(codes):
        description = descriptions.get(code)
        if description is None:
            continue
        session.execute(
            text(
                "INSERT INTO atc_reference (atc_code, atc_description) "
                "VALUES (:code, :description) "
                "ON CONFLICT (atc_code) DO NOTHING"
            ),
            {"code": code, "description": description},
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_enrich.py -v`
Expected: PASS

- [ ] **Step 5: Lint and commit**

```bash
ruff check src/enrich.py tests/test_enrich.py
git add src/enrich.py tests/test_enrich.py
git commit -m "feat: add ATC enrichment with retry-wrapped remote lookup"
```

---

## Task 6: Staging + atomic upsert load (`src/load.py`)

**Files:**
- Create: `src/load.py`
- Test: `tests/test_load.py`

**Interfaces:**
- Consumes: `CleanRecord` from `src.transform` (Task 4).
- Produces: `write_staging(session: Session, records: list[CleanRecord]) -> None`.
- Produces: `publish_staging(session: Session) -> None`.
- Produces: `load_batch(session: Session, records: list[CleanRecord]) -> None` — consumed by `src/pipeline.py` (Task 7). Does not commit; the caller controls the transaction boundary.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_load.py`:

```python
"""SPEC.md §9 — atomic swap leaves a complete table; a mid-load failure leaves it intact."""

from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from src.load import load_batch, write_staging
from src.transform import CleanRecord


def _record(pzn, name, manufacturer_id, price="9.99"):
    return CleanRecord(
        pzn=pzn, name=name, active_ingredient="Paracetamol", dosage_form="tablet",
        strength="500mg", prescription_only=False, price=Decimal(price),
        manufacturer_id=manufacturer_id, atc_code=None,
    )


def _seed_manufacturer(session, name="Nordhealth Pharma") -> int:
    return session.execute(
        text("INSERT INTO manufacturers (name) VALUES (:name) RETURNING manufacturer_id"),
        {"name": name},
    ).scalar_one()


def test_write_staging_populates_staging_table(clean_db):
    manufacturer_id = _seed_manufacturer(clean_db)
    write_staging(clean_db, [_record("11111111", "First", manufacturer_id)])

    rows = clean_db.execute(text("SELECT pzn FROM medications_staging")).all()
    assert [r.pzn for r in rows] == ["11111111"]


def test_load_batch_populates_serving_table(clean_db):
    manufacturer_id = _seed_manufacturer(clean_db)
    records = [
        _record("11111111", "First", manufacturer_id),
        _record("22222222", "Second", manufacturer_id),
    ]

    load_batch(clean_db, records)
    clean_db.commit()

    rows = clean_db.execute(text("SELECT pzn, name FROM medications ORDER BY pzn")).all()
    assert [(r.pzn, r.name) for r in rows] == [("11111111", "First"), ("22222222", "Second")]


def test_load_batch_upserts_changed_rows_idempotently(clean_db):
    manufacturer_id = _seed_manufacturer(clean_db)
    load_batch(clean_db, [_record("11111111", "First", manufacturer_id)])
    clean_db.commit()

    load_batch(clean_db, [_record("11111111", "First Updated", manufacturer_id, price="19.99")])
    clean_db.commit()

    rows = clean_db.execute(text("SELECT pzn, name, price FROM medications")).all()
    assert len(rows) == 1
    assert rows[0].name == "First Updated"
    assert rows[0].price == Decimal("19.99")


def test_repeated_load_of_same_batch_is_idempotent(clean_db):
    manufacturer_id = _seed_manufacturer(clean_db)
    records = [_record("11111111", "First", manufacturer_id)]

    load_batch(clean_db, records)
    clean_db.commit()
    load_batch(clean_db, records)
    clean_db.commit()

    rows = clean_db.execute(text("SELECT pzn FROM medications")).all()
    assert len(rows) == 1


def test_mid_load_failure_leaves_prior_serving_table_intact(clean_db):
    manufacturer_id = _seed_manufacturer(clean_db)
    load_batch(clean_db, [_record("11111111", "Original", manufacturer_id)])
    clean_db.commit()

    bad_batch = [_record("22222222", "Should Not Land", manufacturer_id=999999)]
    with pytest.raises(IntegrityError):
        load_batch(clean_db, bad_batch)
    clean_db.rollback()

    rows = clean_db.execute(text("SELECT pzn, name FROM medications")).all()
    assert [(r.pzn, r.name) for r in rows] == [("11111111", "Original")]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_load.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.load'`

- [ ] **Step 3: Implement `src/load.py`**

```python
"""Staging + atomic upsert into the serving table (SPEC.md §5.5)."""

from sqlalchemy import text
from sqlalchemy.orm import Session

from src.transform import CleanRecord


def write_staging(session: Session, records: list[CleanRecord]) -> None:
    """Truncate `medications_staging` and bulk-insert the clean batch."""
    session.execute(text("TRUNCATE TABLE medications_staging"))
    if not records:
        return
    session.execute(
        text(
            "INSERT INTO medications_staging "
            "(pzn, name, active_ingredient, dosage_form, strength, "
            " prescription_only, price, manufacturer_id, atc_code) "
            "VALUES "
            "(:pzn, :name, :active_ingredient, :dosage_form, :strength, "
            " :prescription_only, :price, :manufacturer_id, :atc_code)"
        ),
        [
            {
                "pzn": r.pzn,
                "name": r.name,
                "active_ingredient": r.active_ingredient,
                "dosage_form": r.dosage_form,
                "strength": r.strength,
                "prescription_only": r.prescription_only,
                "price": r.price,
                "manufacturer_id": r.manufacturer_id,
                "atc_code": r.atc_code,
            }
            for r in records
        ],
    )


def publish_staging(session: Session) -> None:
    """Upsert `medications_staging` into `medications` in a single statement.

    New pzns are inserted; existing pzns are updated in place. Rows already
    in `medications` but absent from this batch are left untouched — correct
    for delta feeds. Re-running the same batch is a no-op diff (idempotent).
    """
    session.execute(
        text(
            "INSERT INTO medications "
            "(pzn, name, active_ingredient, dosage_form, strength, "
            " prescription_only, price, manufacturer_id, atc_code) "
            "SELECT pzn, name, active_ingredient, dosage_form, strength, "
            "       prescription_only, price, manufacturer_id, atc_code "
            "FROM medications_staging "
            "ON CONFLICT (pzn) DO UPDATE SET "
            "  name = EXCLUDED.name, "
            "  active_ingredient = EXCLUDED.active_ingredient, "
            "  dosage_form = EXCLUDED.dosage_form, "
            "  strength = EXCLUDED.strength, "
            "  prescription_only = EXCLUDED.prescription_only, "
            "  price = EXCLUDED.price, "
            "  manufacturer_id = EXCLUDED.manufacturer_id, "
            "  atc_code = EXCLUDED.atc_code"
        )
    )
    session.execute(text("TRUNCATE TABLE medications_staging"))


def load_batch(session: Session, records: list[CleanRecord]) -> None:
    """Write to staging then publish, inside the caller's transaction.

    Caller (src/pipeline.py) commits or rolls back the whole run, so a
    failure anywhere in this function leaves `medications` untouched.
    """
    write_staging(session, records)
    publish_staging(session)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/test_load.py -v`
Expected: PASS

- [ ] **Step 5: Lint and commit**

```bash
ruff check src/load.py tests/test_load.py
git add src/load.py tests/test_load.py
git commit -m "feat: add staging table + atomic upsert publish"
```

---

## Task 7: Lineage + CLI orchestration (`src/lineage.py`, `src/pipeline.py`)

**Files:**
- Create: `src/lineage.py`
- Create: `src/pipeline.py`
- Test: `tests/test_pipeline.py`

**Interfaces:**
- Consumes: `ingest_feed`, `count_dead_letter_rows` (`src.ingest`, Task 3); `deduplicate`, `normalize` (`src.transform`, Task 4); `ensure_atc_reference_rows` (`src.enrich`, Task 5); `load_batch` (`src.load`, Task 6); `get_session_factory` (`src.db`).
- Produces: `start_run(session: Session, source_file: str) -> uuid.UUID`.
- Produces: `content_hash(records: list[CleanRecord]) -> str`.
- Produces: `finish_run(session, run_id, *, rows_in: int, rows_out: int, rows_rejected: int, status: str, content_hash_value: str | None) -> None`.
- Produces: `run_pipeline(feed_path: Path, dead_letter_dir: Path = Path("dead_letter")) -> int` — returns 0 on success, 1 on failure.
- Produces: `main(argv: list[str] | None = None) -> int` — CLI entrypoint for `python -m src.pipeline --feed <path>`.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_pipeline.py`:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_pipeline.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.pipeline'`

- [ ] **Step 3: Implement `src/lineage.py`**

```python
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
```

- [ ] **Step 4: Implement `src/pipeline.py`**

```python
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
    run_id = start_run(session, source_file=str(feed_path))
    session.commit()  # run row is visible even if the rest of the run fails

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
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `pytest tests/test_pipeline.py -v`
Expected: PASS

- [ ] **Step 6: Manually verify the CLI end-to-end**

```bash
docker-compose up -d postgres
psql "$DATABASE_URL" -f migrations/001_init.sql   # if not already applied
python -m src.pipeline --feed data/feed_v1.csv
python -m src.pipeline --feed data/feed_v2_delta.csv
python -m src.pipeline --feed data/feed_broken.csv
```

Expected: exit code 0 for all three; `dead_letter/<run_id>.csv` written for the `feed_broken.csv` run with 6 data rows.

- [ ] **Step 7: Lint and commit**

```bash
ruff check src/lineage.py src/pipeline.py tests/test_pipeline.py
git add src/lineage.py src/pipeline.py tests/test_pipeline.py
git commit -m "feat: add lineage tracking and pipeline CLI orchestration"
```

---

## Task 8: FastAPI serving layer (`src/api/main.py`)

**Files:**
- Create: `src/api/__init__.py`
- Create: `src/api/main.py`
- Modify: `Dockerfile` (drop the stale "does not exist yet" comment)
- Test: `tests/test_api.py`

**Interfaces:**
- Consumes: `get_db` from `src.db`; `ALLOWED_DOSAGE_FORMS` from `src.schema`.
- Produces: `app` (FastAPI instance) at `src.api.main:app`, matching the `Dockerfile`'s existing `uvicorn` CMD.
- Produces: `MedicationOut`, `MedicationListResponse` (Pydantic response models).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_api.py`:

```python
"""SPEC.md §9 — every API endpoint, 404, empty search, filters, pagination, 422."""

from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from src.api.main import app
from src.db import get_db


@pytest.fixture
def client(clean_db):
    def _override_get_db():
        yield clean_db

    app.dependency_overrides[get_db] = _override_get_db
    yield TestClient(app)
    app.dependency_overrides.clear()


def _seed_medication(session, pzn, name, dosage_form="tablet", prescription_only=False, price="9.99"):
    manufacturer_id = session.execute(
        text(
            "INSERT INTO manufacturers (name) VALUES (:name) "
            "ON CONFLICT (name) DO UPDATE SET name = EXCLUDED.name "
            "RETURNING manufacturer_id"
        ),
        {"name": "Nordhealth Pharma"},
    ).scalar_one()
    session.execute(
        text(
            "INSERT INTO medications "
            "(pzn, name, active_ingredient, dosage_form, strength, "
            " prescription_only, price, manufacturer_id, atc_code) "
            "VALUES (:pzn, :name, 'Paracetamol', :dosage_form, '500mg', "
            "        :prescription_only, :price, :manufacturer_id, NULL)"
        ),
        {
            "pzn": pzn, "name": name, "dosage_form": dosage_form,
            "prescription_only": prescription_only, "price": Decimal(price),
            "manufacturer_id": manufacturer_id,
        },
    )
    session.commit()


def test_health_returns_ok(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_list_medications_returns_pagination_metadata(clean_db, client):
    _seed_medication(clean_db, "11111111", "Alpha")
    _seed_medication(clean_db, "22222222", "Beta")

    response = client.get("/v1/medications")

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 2
    assert body["limit"] == 50
    assert body["offset"] == 0
    assert len(body["items"]) == 2


def test_list_medications_filters_by_dosage_form(clean_db, client):
    _seed_medication(clean_db, "11111111", "Alpha", dosage_form="tablet")
    _seed_medication(clean_db, "22222222", "Beta", dosage_form="cream")

    response = client.get("/v1/medications", params={"dosage_form": "cream"})

    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["pzn"] == "22222222"


def test_list_medications_filters_by_prescription_only(clean_db, client):
    _seed_medication(clean_db, "11111111", "Alpha", prescription_only=True)
    _seed_medication(clean_db, "22222222", "Beta", prescription_only=False)

    response = client.get("/v1/medications", params={"prescription_only": "true"})

    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["pzn"] == "11111111"


def test_list_medications_pagination_bounds(clean_db, client):
    for i in range(3):
        _seed_medication(clean_db, f"1111111{i}", f"Med{i}")

    response = client.get("/v1/medications", params={"limit": 2, "offset": 1})

    body = response.json()
    assert body["limit"] == 2
    assert body["offset"] == 1
    assert len(body["items"]) == 2


def test_list_medications_rejects_invalid_dosage_form(client):
    response = client.get("/v1/medications", params={"dosage_form": "lozenge"})
    assert response.status_code == 422


def test_list_medications_rejects_limit_over_max(client):
    response = client.get("/v1/medications", params={"limit": 501})
    assert response.status_code == 422


def test_search_matches_name_case_insensitively(clean_db, client):
    _seed_medication(clean_db, "11111111", "Ibuprofen Forte")

    response = client.get("/v1/medications/search", params={"q": "ibuprofen"})

    body = response.json()
    assert body["total"] == 1
    assert body["items"][0]["pzn"] == "11111111"


def test_search_returns_empty_when_no_match(clean_db, client):
    _seed_medication(clean_db, "11111111", "Alpha")

    response = client.get("/v1/medications/search", params={"q": "nonexistent"})

    assert response.status_code == 200
    assert response.json()["items"] == []


def test_get_medication_by_pzn(clean_db, client):
    _seed_medication(clean_db, "11111111", "Alpha")

    response = client.get("/v1/medications/11111111")

    assert response.status_code == 200
    assert response.json()["name"] == "Alpha"


def test_get_medication_returns_404_for_unknown_pzn(client):
    response = client.get("/v1/medications/99999999")
    assert response.status_code == 404


def test_search_route_takes_precedence_over_pzn_route(client):
    response = client.get("/v1/medications/search", params={"q": "x"})
    assert response.status_code == 200


def test_stats_dosage_forms_groups_correctly(clean_db, client):
    _seed_medication(clean_db, "11111111", "Alpha", dosage_form="tablet")
    _seed_medication(clean_db, "22222222", "Beta", dosage_form="tablet")
    _seed_medication(clean_db, "33333333", "Gamma", dosage_form="cream")

    response = client.get("/v1/stats/dosage-forms")

    assert response.status_code == 200
    assert response.json() == {"tablet": 2, "cream": 1}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/test_api.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'src.api'`

- [ ] **Step 3: Implement `src/api/__init__.py`**

Empty file — marks `src/api` as a package, consistent with `src/__init__.py`.

- [ ] **Step 4: Implement `src/api/main.py`**

```python
"""FastAPI serving layer (SPEC.md §6). Read-only over the `medications` table."""

from decimal import Decimal

from fastapi import Depends, FastAPI, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from src.db import get_db
from src.schema import ALLOWED_DOSAGE_FORMS

app = FastAPI(title="medref-pipeline API", version="1.0.0")

MEDICATION_COLUMNS = (
    "pzn, name, active_ingredient, dosage_form, strength, "
    "prescription_only, price, manufacturer_id, atc_code"
)


class MedicationOut(BaseModel):
    pzn: str
    name: str
    active_ingredient: str
    dosage_form: str
    strength: str
    prescription_only: bool
    price: Decimal
    manufacturer_id: int
    atc_code: str | None = None


class MedicationListResponse(BaseModel):
    items: list[MedicationOut]
    total: int
    limit: int
    offset: int


def _row_to_medication(row) -> MedicationOut:
    return MedicationOut(
        pzn=row.pzn, name=row.name, active_ingredient=row.active_ingredient,
        dosage_form=row.dosage_form, strength=row.strength,
        prescription_only=row.prescription_only, price=row.price,
        manufacturer_id=row.manufacturer_id, atc_code=row.atc_code,
    )


@app.get("/health")
def health(db: Session = Depends(get_db)):
    try:
        db.execute(text("SELECT 1"))
    except Exception as exc:
        raise HTTPException(status_code=503, detail="database unavailable") from exc
    return {"status": "ok", "database": "connected"}


@app.get("/v1/medications", response_model=MedicationListResponse)
def list_medications(
    dosage_form: str | None = Query(default=None),
    prescription_only: bool | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    if dosage_form is not None and dosage_form not in ALLOWED_DOSAGE_FORMS:
        raise HTTPException(
            status_code=422, detail=f"dosage_form must be one of {sorted(ALLOWED_DOSAGE_FORMS)}"
        )

    where_clauses = []
    params: dict = {}
    if dosage_form is not None:
        where_clauses.append("dosage_form = :dosage_form")
        params["dosage_form"] = dosage_form
    if prescription_only is not None:
        where_clauses.append("prescription_only = :prescription_only")
        params["prescription_only"] = prescription_only
    where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""

    total = db.execute(text(f"SELECT COUNT(*) FROM medications {where_sql}"), params).scalar_one()
    rows = db.execute(
        text(
            f"SELECT {MEDICATION_COLUMNS} FROM medications {where_sql} "
            "ORDER BY pzn LIMIT :limit OFFSET :offset"
        ),
        {**params, "limit": limit, "offset": offset},
    ).all()
    return MedicationListResponse(
        items=[_row_to_medication(r) for r in rows], total=total, limit=limit, offset=offset
    )


@app.get("/v1/medications/search", response_model=MedicationListResponse)
def search_medications(
    q: str = Query(..., min_length=1),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    pattern = f"%{q}%"
    total = db.execute(
        text(
            "SELECT COUNT(*) FROM medications "
            "WHERE name ILIKE :pattern OR active_ingredient ILIKE :pattern"
        ),
        {"pattern": pattern},
    ).scalar_one()
    rows = db.execute(
        text(
            f"SELECT {MEDICATION_COLUMNS} FROM medications "
            "WHERE name ILIKE :pattern OR active_ingredient ILIKE :pattern "
            "ORDER BY pzn LIMIT :limit OFFSET :offset"
        ),
        {"pattern": pattern, "limit": limit, "offset": offset},
    ).all()
    return MedicationListResponse(
        items=[_row_to_medication(r) for r in rows], total=total, limit=limit, offset=offset
    )


@app.get("/v1/medications/{pzn}", response_model=MedicationOut)
def get_medication(pzn: str, db: Session = Depends(get_db)):
    row = db.execute(
        text(f"SELECT {MEDICATION_COLUMNS} FROM medications WHERE pzn = :pzn"),
        {"pzn": pzn},
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail=f"medication {pzn!r} not found")
    return _row_to_medication(row)


@app.get("/v1/stats/dosage-forms")
def dosage_form_stats(db: Session = Depends(get_db)) -> dict[str, int]:
    rows = db.execute(
        text("SELECT dosage_form, COUNT(*) AS count FROM medications GROUP BY dosage_form")
    ).all()
    return {r.dosage_form: r.count for r in rows}
```

- [ ] **Step 5: Update the Dockerfile comment**

In `Dockerfile`, replace the two-line comment above the `CMD` line with just the `CMD` line (the comment is stale once `src/api/main.py` exists):

```dockerfile
CMD ["uvicorn", "src.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `pytest tests/test_api.py -v`
Expected: PASS

- [ ] **Step 7: Manually verify via docker-compose**

```bash
docker-compose up -d --build
curl localhost:8000/health
curl "localhost:8000/v1/medications?dosage_form=tablet&limit=10"
curl "localhost:8000/v1/medications/search?q=ibuprofen"
curl "localhost:8000/v1/stats/dosage-forms"
```

Expected: 200 responses with JSON bodies matching the response models above (run `python -m src.pipeline --feed data/feed_v1.csv` against the compose DB first if `medications` is still empty).

- [ ] **Step 8: Lint and commit**

```bash
ruff check src/api/main.py tests/test_api.py
git add src/api/__init__.py src/api/main.py Dockerfile tests/test_api.py
git commit -m "feat: add FastAPI serving layer for medications"
```

---

## Task 9: CI workflow + README

**Files:**
- Create: `.github/workflows/ci.yml`
- Create: `README.md`

**Interfaces:**
- Produces: a GitHub Actions workflow that runs `ruff check` and `pytest` against a Postgres 16 service container on every push/PR.
- Produces: `README.md` documenting setup, run commands, and design decisions (SPEC.md §10 acceptance criterion).

- [ ] **Step 1: Write the CI workflow**

Create `.github/workflows/ci.yml`:

```yaml
name: CI

on:
  push:
  pull_request:

jobs:
  lint-test:
    runs-on: ubuntu-latest
    services:
      postgres:
        image: postgres:16
        env:
          POSTGRES_USER: medref
          POSTGRES_PASSWORD: medref
          POSTGRES_DB: medref
        ports:
          - 5432:5432
        options: >-
          --health-cmd "pg_isready -U medref -d medref"
          --health-interval 5s
          --health-timeout 5s
          --health-retries 10

    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"

      - name: Install dependencies
        run: pip install -e ".[dev]"

      - name: Lint
        run: ruff check .

      - name: Run tests
        env:
          DATABASE_URL: postgresql+psycopg://medref:medref@localhost:5432/medref
        run: pytest
```

- [ ] **Step 2: Verify the workflow syntax**

```bash
python -c "import yaml, pathlib; yaml.safe_load(pathlib.Path('.github/workflows/ci.yml').read_text())"
```

Expected: no output (parses cleanly).

- [ ] **Step 3: Write the README**

Create `README.md` covering: project overview and link to `SPEC.md`; setup via `docker-compose up -d` (migrations auto-apply via the mounted init script) plus local dev install (`pip install -e ".[dev]"`) and manual migration apply (`psql "$DATABASE_URL" -f migrations/001_init.sql`) for non-compose Postgres; pipeline run commands for all three feeds plus the `dead_letter/` and `pipeline_runs` lineage outputs; the `scripts/generate_data.py` regeneration command; example `curl` calls for every endpoint plus a pointer to `/docs`; the test command (`docker-compose up -d postgres && pytest`) with a note that tests are real-Postgres integration tests, not mocked, and why; and a "Design decisions" section capturing the same rationale as the Global Constraints section of this plan (stdlib csv over Polars for `pzn` safety; staging+upsert instead of truncate+replace so delta feeds don't wipe untouched rows; idempotency falling out of the upsert; dead-letter vs. dedup being different failure modes, with `feed_broken.csv`'s duplicate-PZN pair explained; the enrich retry wrapper's dependency-injected `fetch_fn`/`sleep_fn` for testability; the API being read-only Core SQL rather than an ORM mapping; and a one-line note that auth is out of scope per SPEC.md §12, with the lightest addition (a static API-key header dependency) named as the extension point). Close with a short "Optional: /v1/ask" section noting it requires `DEEPSEEK_API_KEY` (DeepSeek's OpenAI-compatible chat completions API, chosen because the user holds API credit there rather than with Anthropic) and is implemented in Task 10 of this plan, only after core acceptance criteria pass.

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/ci.yml README.md
git commit -m "docs: add CI workflow and README"
```

---

## Task 10 (optional — only after Tasks 1–9 are complete and green): `/v1/ask` RAG endpoint

DeepSeek's chat completions API is OpenAI-compatible (base URL `https://api.deepseek.com`, model `deepseek-chat`), so this task uses the `openai` SDK pointed at DeepSeek rather than a DeepSeek-specific client.

**Files:**
- Modify: `pyproject.toml` (add `openai` dependency)
- Modify: `src/api/main.py` (add endpoint)
- Test: `tests/test_api.py` (add one test)

**Interfaces:**
- Consumes: `get_settings` from `src.config` (already has `deepseek_api_key`); `app`, `get_db`, `MEDICATION_COLUMNS` from `src.api.main`.
- Produces: `POST /v1/ask` — body `{"question": str}`, response `{"answer": str, "pzns_cited": list[str]}`.

- [ ] **Step 1: Add the dependency**

In `pyproject.toml`, add to `dependencies`:

```toml
    "openai>=1.40",
```

- [ ] **Step 2: Write the failing test**

Append to `tests/test_api.py`:

```python
def test_ask_endpoint_requires_api_key(client, monkeypatch):
    class _NoKeySettings:
        deepseek_api_key = None

    monkeypatch.setattr("src.api.main.get_settings", lambda: _NoKeySettings())
    response = client.post("/v1/ask", json={"question": "What treats headaches?"})
    assert response.status_code == 503
```

- [ ] **Step 3: Run to verify it fails**

Run: `pytest tests/test_api.py::test_ask_endpoint_requires_api_key -v`
Expected: FAIL (404, route doesn't exist yet)

- [ ] **Step 4: Implement the endpoint**

Add to the top of `src/api/main.py`:

```python
from openai import OpenAI

from src.config import get_settings

DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEEPSEEK_MODEL = "deepseek-chat"
```

Append to `src/api/main.py`:

```python
class AskRequest(BaseModel):
    question: str


class AskResponse(BaseModel):
    answer: str
    pzns_cited: list[str]


def _retrieve_candidates(db: Session, question: str, limit: int = 5):
    pattern = f"%{question}%"
    rows = db.execute(
        text(
            f"SELECT {MEDICATION_COLUMNS} FROM medications "
            "WHERE name ILIKE :pattern OR active_ingredient ILIKE :pattern "
            "LIMIT :limit"
        ),
        {"pattern": pattern, "limit": limit},
    ).all()
    if not rows:
        rows = db.execute(
            text(f"SELECT {MEDICATION_COLUMNS} FROM medications LIMIT :limit"),
            {"limit": limit},
        ).all()
    return rows


@app.post("/v1/ask", response_model=AskResponse)
def ask(request: AskRequest, db: Session = Depends(get_db)):
    settings = get_settings()
    if not settings.deepseek_api_key:
        raise HTTPException(status_code=503, detail="DEEPSEEK_API_KEY is not configured")

    candidates = _retrieve_candidates(db, request.question)
    context = "\n".join(
        f"- PZN {r.pzn}: {r.name} ({r.active_ingredient}, {r.dosage_form}, {r.strength}, "
        f"prescription_only={r.prescription_only}, price={r.price})"
        for r in candidates
    )
    client = OpenAI(api_key=settings.deepseek_api_key, base_url=DEEPSEEK_BASE_URL)
    completion = client.chat.completions.create(
        model=DEEPSEEK_MODEL,
        max_tokens=512,
        messages=[
            {
                "role": "user",
                "content": (
                    "Answer the question using only the medication rows below. "
                    "Cite the PZN(s) you used.\n\n"
                    f"Medications:\n{context}\n\nQuestion: {request.question}"
                ),
            }
        ],
    )
    answer_text = completion.choices[0].message.content
    cited = [r.pzn for r in candidates if r.pzn in answer_text]
    return AskResponse(answer=answer_text, pzns_cited=cited)
```

- [ ] **Step 5: Run to verify it passes**

Run: `pytest tests/test_api.py::test_ask_endpoint_requires_api_key -v`
Expected: PASS

- [ ] **Step 6: Lint and commit**

```bash
ruff check src/api/main.py tests/test_api.py
git add pyproject.toml src/api/main.py tests/test_api.py
git commit -m "feat: add optional /v1/ask RAG endpoint"
```
