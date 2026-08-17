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
def health(db: Session = Depends(get_db)):  # noqa: B008
    try:
        db.execute(text("SELECT 1"))
    except Exception as exc:
        raise HTTPException(status_code=503, detail="database unavailable") from exc
    return {"status": "ok", "database": "connected"}


@app.get("/v1/medications", response_model=MedicationListResponse)
def list_medications(
    dosage_form: str | None = Query(default=None),  # noqa: B008
    prescription_only: bool | None = Query(default=None),  # noqa: B008
    limit: int = Query(default=50, ge=1, le=500),  # noqa: B008
    offset: int = Query(default=0, ge=0),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
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
    q: str = Query(..., min_length=1),  # noqa: B008
    limit: int = Query(default=50, ge=1, le=500),  # noqa: B008
    offset: int = Query(default=0, ge=0),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
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
def get_medication(pzn: str, db: Session = Depends(get_db)):  # noqa: B008
    row = db.execute(
        text(f"SELECT {MEDICATION_COLUMNS} FROM medications WHERE pzn = :pzn"),
        {"pzn": pzn},
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail=f"medication {pzn!r} not found")
    return _row_to_medication(row)


@app.get("/v1/stats/dosage-forms")
def dosage_form_stats(db: Session = Depends(get_db)) -> dict[str, int]:  # noqa: B008
    rows = db.execute(
        text("SELECT dosage_form, COUNT(*) AS count FROM medications GROUP BY dosage_form")
    ).all()
    return {r.dosage_form: r.count for r in rows}
