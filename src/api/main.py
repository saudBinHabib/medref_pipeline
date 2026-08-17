"""FastAPI serving layer (SPEC.md §6). Read-only over the `medications` table."""

import re

from fastapi import Depends, FastAPI, HTTPException, Query
from openai import OpenAI
from pydantic import BaseModel, RootModel
from sqlalchemy import text
from sqlalchemy.orm import Session

from src.config import get_settings
from src.db import get_db
from src.schema import ALLOWED_DOSAGE_FORMS, Medication

DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEEPSEEK_MODEL = "deepseek-chat"

app = FastAPI(title="medref-pipeline API", version="1.0.0")

MEDICATION_COLUMNS = (
    "pzn, name, active_ingredient, dosage_form, strength, "
    "prescription_only, price, manufacturer_id, atc_code"
)

# Fixed, static SQL fragments (SPEC.md §7: no string-interpolated queries).
# Every query below is assembled by concatenating/joining these named
# constants — never by interpolating request data into SQL text. Bound
# values always travel via `:param` placeholders.
_MEDICATIONS_SELECT = "SELECT " + MEDICATION_COLUMNS + " FROM medications"
_MEDICATIONS_COUNT = "SELECT COUNT(*) FROM medications"
_ORDER_BY_PZN = " ORDER BY pzn"
_LIMIT_OFFSET = " LIMIT :limit OFFSET :offset"

# LIKE/ILIKE metacharacters must be escaped so user-supplied search text is
# matched literally, never as a wildcard (SPEC.md §7 / avoid surprising
# matches on `%`, `_`, `\`).
_LIKE_ESCAPE_CHAR = "\\"
_LIKE_ESCAPE_CLAUSE = f" ESCAPE '{_LIKE_ESCAPE_CHAR}'"


def _escape_like_term(term: str) -> str:
    """Escape LIKE/ILIKE wildcard metacharacters in user-supplied text."""
    return (
        term.replace(_LIKE_ESCAPE_CHAR, _LIKE_ESCAPE_CHAR * 2)
        .replace("%", _LIKE_ESCAPE_CHAR + "%")
        .replace("_", _LIKE_ESCAPE_CHAR + "_")
    )


def _contains_pattern(term: str) -> str:
    """Build a `%term%` ILIKE pattern with `term` matched literally."""
    return f"%{_escape_like_term(term)}%"


def _where_sql(clauses: list[str]) -> str:
    """Join a list of static, parameterized clause fragments into a WHERE clause."""
    if not clauses:
        return ""
    return " WHERE " + " AND ".join(clauses)


class MedicationListResponse(BaseModel):
    items: list[Medication]
    total: int
    limit: int
    offset: int


class HealthResponse(BaseModel):
    status: str
    database: str


class DosageFormStatsResponse(RootModel[dict[str, int]]):
    """`{dosage_form: count}` (SPEC.md §6)."""


def _row_to_medication(row) -> Medication:
    return Medication(
        pzn=row.pzn, name=row.name, active_ingredient=row.active_ingredient,
        dosage_form=row.dosage_form, strength=row.strength,
        prescription_only=row.prescription_only, price=row.price,
        manufacturer_id=row.manufacturer_id, atc_code=row.atc_code,
    )


@app.get("/health", response_model=HealthResponse)
def health(db: Session = Depends(get_db)):  # noqa: B008
    try:
        db.execute(text("SELECT 1"))
    except Exception as exc:
        raise HTTPException(status_code=503, detail="database unavailable") from exc
    return HealthResponse(status="ok", database="connected")


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
    where_sql = _where_sql(where_clauses)

    total = db.execute(text(_MEDICATIONS_COUNT + where_sql), params).scalar_one()
    rows = db.execute(
        text(_MEDICATIONS_SELECT + where_sql + _ORDER_BY_PZN + _LIMIT_OFFSET),
        {**params, "limit": limit, "offset": offset},
    ).all()
    return MedicationListResponse(
        items=[_row_to_medication(r) for r in rows], total=total, limit=limit, offset=offset
    )


_SEARCH_WHERE = (
    " WHERE name ILIKE :pattern" + _LIKE_ESCAPE_CLAUSE
    + " OR active_ingredient ILIKE :pattern" + _LIKE_ESCAPE_CLAUSE
)


@app.get("/v1/medications/search", response_model=MedicationListResponse)
def search_medications(
    q: str = Query(..., min_length=1),  # noqa: B008
    limit: int = Query(default=50, ge=1, le=500),  # noqa: B008
    offset: int = Query(default=0, ge=0),  # noqa: B008
    db: Session = Depends(get_db),  # noqa: B008
):
    pattern = _contains_pattern(q)
    total = db.execute(
        text(_MEDICATIONS_COUNT + _SEARCH_WHERE),
        {"pattern": pattern},
    ).scalar_one()
    rows = db.execute(
        text(_MEDICATIONS_SELECT + _SEARCH_WHERE + _ORDER_BY_PZN + _LIMIT_OFFSET),
        {"pattern": pattern, "limit": limit, "offset": offset},
    ).all()
    return MedicationListResponse(
        items=[_row_to_medication(r) for r in rows], total=total, limit=limit, offset=offset
    )


@app.get("/v1/medications/{pzn}", response_model=Medication)
def get_medication(pzn: str, db: Session = Depends(get_db)):  # noqa: B008
    row = db.execute(
        text(_MEDICATIONS_SELECT + " WHERE pzn = :pzn"),
        {"pzn": pzn},
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail=f"medication {pzn!r} not found")
    return _row_to_medication(row)


@app.get("/v1/stats/dosage-forms", response_model=DosageFormStatsResponse)
def dosage_form_stats(db: Session = Depends(get_db)):  # noqa: B008
    rows = db.execute(
        text("SELECT dosage_form, COUNT(*) AS count FROM medications GROUP BY dosage_form")
    ).all()
    return DosageFormStatsResponse({r.dosage_form: r.count for r in rows})


class AskRequest(BaseModel):
    question: str


class AskResponse(BaseModel):
    answer: str
    pzns_cited: list[str]


# Very short/common words carry no retrieval signal and would just widen the
# ILIKE match to everything; drop them before building the token query.
_ASK_STOPWORDS = {
    "the", "a", "an", "is", "are", "was", "were", "for", "of", "in", "on",
    "to", "and", "or", "what", "which", "who", "whom", "does", "do", "did",
    "can", "with", "about", "treat", "treats", "used", "use", "this", "that",
}


def _retrieve_candidates(db: Session, question: str, limit: int = 5):
    """Find medication rows relevant to a free-text question.

    Tokenizes the question and ILIKE-matches each meaningful token against
    name or active_ingredient (OR'd together), rather than matching the
    entire raw sentence as one literal substring (which essentially never
    matches a medication name). Falls back to an unfiltered, deterministically
    ordered page when no token matches anything.
    """
    tokens = [t for t in re.findall(r"[A-Za-z0-9]+", question.lower()) if len(t) > 2]
    tokens = [t for t in tokens if t not in _ASK_STOPWORDS]

    rows = []
    if tokens:
        def _token_clause(param_name: str) -> str:
            # Static clause shape, one instance per token; `param_name` is a
            # generated *parameter name* (e.g. "tok0") — the token text
            # itself is never embedded here, only bound via `params` below
            # (escaped, so it is matched literally, not as a wildcard).
            return (
                "name ILIKE :" + param_name + _LIKE_ESCAPE_CLAUSE
                + " OR active_ingredient ILIKE :" + param_name + _LIKE_ESCAPE_CLAUSE
            )

        param_names = [f"tok{i}" for i in range(len(tokens))]
        match_clauses = " OR ".join(_token_clause(name) for name in param_names)
        params = {
            name: _contains_pattern(tok)
            for name, tok in zip(param_names, tokens, strict=True)
        }
        rows = db.execute(
            text(_MEDICATIONS_SELECT + " WHERE " + match_clauses + _ORDER_BY_PZN + " LIMIT :limit"),
            {**params, "limit": limit},
        ).all()
    if not rows:
        rows = db.execute(
            text(_MEDICATIONS_SELECT + _ORDER_BY_PZN + " LIMIT :limit"),
            {"limit": limit},
        ).all()
    return rows


@app.post("/v1/ask", response_model=AskResponse)
def ask(request: AskRequest, db: Session = Depends(get_db)):  # noqa: B008
    settings = get_settings()
    if not settings.deepseek_api_key:
        raise HTTPException(status_code=503, detail="DEEPSEEK_API_KEY is not configured")

    candidates = _retrieve_candidates(db, request.question)
    context = "\n".join(
        f"- PZN {r.pzn}: {r.name} ({r.active_ingredient}, {r.dosage_form}, {r.strength}, "
        f"prescription_only={r.prescription_only}, price={r.price})"
        for r in candidates
    )
    # timeout=: this route handler is sync, so FastAPI runs it on a bounded
    # threadpool — an unbounded hung upstream call would tie up a worker slot.
    client = OpenAI(api_key=settings.deepseek_api_key, base_url=DEEPSEEK_BASE_URL, timeout=30.0)
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
    # The OpenAI SDK types completion content as `str | None`; a null/empty
    # completion must not crash the request with an unhandled TypeError.
    if not answer_text:
        raise HTTPException(status_code=502, detail="LLM returned an empty completion")
    cited = [r.pzn for r in candidates if r.pzn in answer_text]
    return AskResponse(answer=answer_text, pzns_cited=cited)
