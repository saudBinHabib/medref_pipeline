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


def _seed_medication(
    session, pzn, name, dosage_form="tablet", prescription_only=False, price="9.99"
):
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
