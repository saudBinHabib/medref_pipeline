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
