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

BRAND_PREFIXES = [
    "Medo", "Curalin", "Biovex", "Pharmatec", "Sanavia", "Novapex", "Rekura", "Vitapharm",
]

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
