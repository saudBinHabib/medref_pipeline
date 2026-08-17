# medref API Guide

How to run `medref-pipeline`, access its database, and consume every endpoint. Every example below is a real request against the running service — the responses shown are real output, not mocks.

## Contents

- [Quickstart](#quickstart)
- [Base URL & interactive docs](#base-url--interactive-docs)
- [What docker-compose starts](#what-docker-compose-starts)
- [Running the pipeline](#running-the-pipeline)
- [Docker Compose command reference](#docker-compose-command-reference)
- [Accessing the database directly](#accessing-the-database-directly)
- [Endpoints](#endpoints)
  - [GET /health](#get-health)
  - [GET /v1/medications](#get-v1medications)
  - [GET /v1/medications/search](#get-v1medicationssearch)
  - [GET /v1/medications/{pzn}](#get-v1medicationspzn)
  - [GET /v1/stats/dosage-forms](#get-v1statsdosage-forms)
  - [POST /v1/ask (optional)](#post-v1ask-optional)
- [Error responses](#error-responses)
- [A complete example](#a-complete-example)

---

## Quickstart

Three steps, in order: start the services, put some data in the database, then call the API. If step 2 is skipped, the API is up but every list comes back empty — that's the single most common "why is nothing returned" moment.

**1. Start Postgres + the API**

From the repo root. This builds and starts both containers; the API won't accept traffic until Postgres reports healthy, which the compose file waits for automatically.

```bash
cp .env.example .env
docker-compose up -d --build
```

**2. Load a feed into the database**

The API only serves what's in the `medications` table. Run the pipeline once against the sample feed to populate it — this can be run again any time to refresh or update the data.

```bash
python -m src.pipeline --feed data/feed_v1.csv
```

**3. Call the API**

Plain HTTP on port 8000. No headers, no API key, no request body for any of the read endpoints.

```bash
curl http://localhost:8000/health
```

```json
{"status":"ok","database":"connected"}
```

---

## Base URL & interactive docs

Every path below is relative to:

```
http://localhost:8000
```

The service also auto-generates an interactive explorer — open `http://localhost:8000/docs` in a browser and you can fire requests directly from the page without writing any code at all. It's the fastest way to poke at the API before wiring up a script.

> **Not sure what "consuming an API" means?** It just means your code sends an HTTP request to a URL and reads back the JSON text in the response — the same thing a browser does when it loads a page, except you get structured data back instead of HTML. Every example below shows the exact request and the exact JSON it returns.

---

## What docker-compose starts

Running `docker-compose up -d` starts exactly two containers, defined in `docker-compose.yml`. Nothing else is implied or auto-started — no worker, no scheduler, no admin UI.

| Service | Image | Port on your machine | What it is |
|---|---|---|---|
| `postgres` | `postgres:16` | `5432` | The database. On its very first boot it auto-runs `migrations/001_init.sql`, which creates all five tables. On every later boot it just mounts the same data volume (`pgdata`) and skips that step. |
| `api` | built from `./Dockerfile` | `8000` | The FastAPI app you'll be calling below (`uvicorn src.api.main:app`). It waits for postgres's healthcheck to pass before it starts, and talks to it over the internal docker network at hostname `postgres`. |

Both read their settings from the same `.env` file at the repo root (via `env_file:` in the compose file) — `POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB`, `API_PORT`, and so on. Edit `.env`, then `docker-compose up -d` again to apply the change (add `--build` if you also changed application code, not just env values).

> **The pipeline is not one of these two services.** Look at `Dockerfile`: it only `COPY`s `pyproject.toml` and `src/` into the image — not `data/`, not `migrations/`. That's deliberate: the `api` container's only job is to serve HTTP. The CSV feeds and the pipeline CLI live on your host machine instead — see the next section.

---

## Running the pipeline

The pipeline is a Python CLI you run on your host, not inside a container. It reads a feed CSV, validates and cleans it, and writes into the same Postgres that the `postgres` container exposes on `localhost:5432` — so both "sides" (your host CLI, and the API in its container) end up pointed at one shared database.

**One-time setup:**

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

**Run it against any of the three sample feeds:**

```bash
python -m src.pipeline --feed data/feed_v1.csv        # 80 fresh rows
python -m src.pipeline --feed data/feed_v2_delta.csv  # updates + new rows, on top of v1
python -m src.pipeline --feed data/feed_broken.csv    # rows with deliberate errors, to see dead-lettering
```

Each run prints its progress and exits `0` on success, non-zero on failure. Re-running the same feed is safe — rows are upserted by PZN, so nothing duplicates.

| Stage | Module | What it does |
|---|---|---|
| ingest | `src/ingest.py` | Streams the CSV row by row, validates each row's shape (PZN format, dosage form, price, etc.). Bad rows are written to `dead_letter/<run_id>.csv` with a reason; the run keeps going. |
| transform | `src/transform.py` | Drops duplicate PZNs within the feed (last one wins) and resolves each manufacturer name to an id, creating new manufacturers as needed. |
| enrich | `src/enrich.py` | Looks up each row's ATC code against `data/atc_reference.csv`. If `ATC_REMOTE_LOOKUP=true` in `.env`, codes not found offline are additionally looked up via a remote terminology API (`ATC_REMOTE_API_URL`) with retry/backoff before being given up on. A code that's still unresolved is excluded from the batch and dead-lettered, rather than failing the whole run. |
| load | `src/load.py` | Writes the clean batch into a staging table, then atomically upserts it into the live `medications` table in one transaction. |
| lineage | `src/lineage.py` | Records one row per run in `pipeline_runs` — how many rows came in, how many loaded, how many were rejected, and whether it succeeded. |

You can watch every run's history — including ones that failed — directly in the database; see [Accessing the database directly](#accessing-the-database-directly) below.

---

## Docker Compose command reference

Everything else you'd typically want to do — check status, see logs, restart, wipe and start over — is one `docker-compose` (or `docker compose`, both work) command away. Run these from the repo root.

| Command | What it does |
|---|---|
| `docker-compose up -d` | Start both services in the background. Safe to run repeatedly — already-running containers are left alone. |
| `docker-compose up -d --build` | Same, but rebuilds the `api` image first. Use this after you change anything in `src/`. |
| `docker-compose ps` | Show both containers' status, including whether postgres has passed its healthcheck yet. |
| `docker-compose logs -f api` | Stream the API's logs live (request logs, errors, startup messages). `Ctrl+C` to stop watching. |
| `docker-compose logs -f postgres` | Stream Postgres's logs live. |
| `docker-compose restart api` | Restart just the API process — quicker than a full rebuild if you only need to pick up an env change. |
| `docker-compose exec postgres psql -U medref -d medref` | Open an interactive `psql` shell inside the database container. No local Postgres client install needed. See [next section](#accessing-the-database-directly). |
| `docker-compose exec api bash` | Open a shell inside the running API container — useful for checking what's actually installed or confirming an env var made it in. |
| `docker-compose stop` | Stop both containers but keep them (and the `pgdata` volume) around for next time. |
| `docker-compose down` | Stop *and remove* both containers. The `pgdata` volume — your actual data — survives this. |
| `docker-compose down -v` | Stop, remove the containers, **and delete the `pgdata` volume**. This wipes the database completely — use it when you want a genuinely clean slate (you'll need to re-run the pipeline afterward). |

---

## Accessing the database directly

The API is the intended interface, but nothing stops you from talking to Postgres yourself — it's a normal Postgres instance exposed on your host at `localhost:5432`. Two ways in:

**Option A — psql, no install required**

```bash
docker-compose exec postgres psql -U medref -d medref
```

This opens an interactive SQL shell running *inside the container*, so it always works regardless of what's installed on your host.

**Option B — any GUI client or script, from your host**

Point any Postgres client (DBeaver, TablePlus, Postico, pgAdmin, or a Python/psycopg script) at these values — they're the defaults from `.env.example`; check your own `.env` if you changed them:

| Field | Value |
|---|---|
| Host | `localhost` |
| Port | `5432` |
| Database | `medref` |
| Username | `medref` |
| Password | `medref` |

**The five tables:**

```
medref=# \dt
               List of relations
 Schema |        Name         | Type  | Owner
--------+---------------------+-------+--------
 public | atc_reference       | table | medref
 public | manufacturers       | table | medref
 public | medications         | table | medref
 public | medications_staging | table | medref
 public | pipeline_runs       | table | medref
(5 rows)
```

| Table | What's in it |
|---|---|
| `medications` | The live, serving table — this is exactly what the API reads. Every endpoint below is a query against this table. |
| `medications_staging` | An internal scratch table the pipeline writes to mid-load, then upserts from. It's empty between runs — not meant to be queried directly. |
| `manufacturers` | Manufacturer name → id lookup, built up automatically as feeds are loaded. |
| `atc_reference` | ATC drug-classification codes and their descriptions, from `data/atc_reference.csv`. |
| `pipeline_runs` | One row per pipeline run: how many rows came in, loaded, and were rejected, and whether it succeeded — the run history mentioned above. |

> **Want the relationships at a glance?** See the [entity-relationship diagram](../README.md#data-model) in the main README for how these five tables connect (foreign keys, primary keys, and which columns are checked/enum-constrained).

**A couple of queries worth knowing:**

```sql
-- how many medications are currently served?
SELECT COUNT(*) FROM medications;

-- most recent pipeline runs, newest first
SELECT source_file, status, rows_in, rows_out, rows_rejected, started_at
FROM pipeline_runs
ORDER BY started_at DESC
LIMIT 5;
```

> **Prefer Python over SQL?** The same connection works with `psycopg` or SQLAlchemy: `psycopg.connect("postgresql://medref:medref@localhost:5432/medref")`. But for anything read-facing, going through the API is usually simpler — it already handles pagination, filtering, and validation for you.

---

## Endpoints

Six endpoints total. The four list/search/get endpoints all return the same medication shape; the two extras are a health check and an optional natural-language question-answering endpoint.

### GET /health

Confirms the API process is up *and* can reach the database. Use this before anything else — if this fails, no other endpoint will work either.

```bash
curl http://localhost:8000/health
```

```python
import requests

r = requests.get("http://localhost:8000/health")
print(r.status_code, r.json())
```

```javascript
const res = await fetch("http://localhost:8000/health");
console.log(res.status, await res.json());
```

**Response — 200 OK**

```json
{"status":"ok","database":"connected"}
```

---

### GET /v1/medications

Lists medications, ordered by PZN, with optional filters and pagination. This is the endpoint to reach for when you want "all tablets" or "everything, 50 at a time" rather than one specific item.

| Query param | Type | | Meaning |
|---|---|---|---|
| `dosage_form` | string | optional | One of `tablet`, `capsule`, `solution`, `injection`, `cream`, `drops`, `spray`. Anything else returns `422`. |
| `prescription_only` | boolean | optional | `true` or `false`. |
| `limit` | integer | optional | Page size. Default `50`, max `500`. |
| `offset` | integer | optional | How many rows to skip. Default `0`. Increase by `limit` each page to page through results. |

```bash
curl "http://localhost:8000/v1/medications?dosage_form=tablet&limit=2"
```

```python
import requests

r = requests.get(
    "http://localhost:8000/v1/medications",
    params={"dosage_form": "tablet", "limit": 2},
)
data = r.json()
for med in data["items"]:
    print(med["pzn"], med["name"], med["price"])
```

```javascript
const url = new URL("http://localhost:8000/v1/medications");
url.searchParams.set("dosage_form", "tablet");
url.searchParams.set("limit", "2");

const data = await (await fetch(url)).json();
data.items.forEach((m) => console.log(m.pzn, m.name, m.price));
```

**Response — 200 OK**

```json
{
  "items": [
    {
      "pzn": "00000001",
      "name": "Sanavia Metformin",
      "active_ingredient": "Metformin",
      "dosage_form": "tablet",
      "strength": "200mg",
      "prescription_only": true,
      "price": "14.71",
      "manufacturer_id": 2,
      "atc_code": "A10BA02"
    },
    {
      "pzn": "00000002",
      "name": "Rekura Metoclopramide",
      "active_ingredient": "Metoclopramide",
      "dosage_form": "tablet",
      "strength": "100mg",
      "prescription_only": true,
      "price": "10.70",
      "manufacturer_id": 3,
      "atc_code": "A03FA01"
    }
  ],
  "total": 15,
  "limit": 2,
  "offset": 0
}
```

> **Reading the envelope:** `items` is the page you asked for. `total` is how many rows match your filters *across all pages* — that's what tells you whether there's more to fetch. To get the next page, call again with `offset` increased by `limit`, and stop once `offset ≥ total`.

---

### GET /v1/medications/search

Free-text search across `name` and `active_ingredient`, case-insensitive. Use this when you know roughly what you're looking for by name, not by exact PZN.

| Query param | Type | | Meaning |
|---|---|---|---|
| `q` | string | required | Search term. Matches anywhere in the name or active ingredient (a substring match, not an exact one). |
| `limit` | integer | optional | Same as above — default `50`, max `500`. |
| `offset` | integer | optional | Same as above. |

```bash
curl "http://localhost:8000/v1/medications/search?q=metformin"
```

```python
import requests

r = requests.get(
    "http://localhost:8000/v1/medications/search",
    params={"q": "metformin"},
)
print(r.json()["total"], "matches")
```

```javascript
const url = new URL("http://localhost:8000/v1/medications/search");
url.searchParams.set("q", "metformin");

const data = await (await fetch(url)).json();
console.log(data.total, "matches");
```

**Response — 200 OK (4 matches, showing shape)**

```json
{
  "items": [
    { "pzn": "00000001", "name": "Sanavia Metformin", "active_ingredient": "Metformin", "...": "..." },
    { "pzn": "00000030", "name": "Rekura Metformin",  "active_ingredient": "Metformin", "...": "..." }
  ],
  "total": 4,
  "limit": 50,
  "offset": 0
}
```

A search with no matches is still a **200** with `"items": []` — an empty result is not an error.

> **Special characters are matched literally.** `%`, `_`, and `\` in `q` are escaped automatically before the search runs, so `q=50%` matches a literal `%` character in a name/active ingredient rather than acting as a wildcard. You never need to escape these yourself.

---

### GET /v1/medications/{pzn}

Fetch exactly one medication when you already know its PZN — the 8-digit product code that's the primary key for every record. This is a path parameter (part of the URL), not a query string.

```bash
curl http://localhost:8000/v1/medications/00000001
```

```python
import requests

pzn = "00000001"
r = requests.get(f"http://localhost:8000/v1/medications/{pzn}")
if r.status_code == 404:
    print("no medication with that PZN")
else:
    print(r.json())
```

```javascript
const pzn = "00000001";
const res = await fetch(`http://localhost:8000/v1/medications/${pzn}`);
if (res.status === 404) {
  console.log("no medication with that PZN");
} else {
  console.log(await res.json());
}
```

**Response — 200 OK**

```json
{
  "pzn": "00000001",
  "name": "Sanavia Metformin",
  "active_ingredient": "Metformin",
  "dosage_form": "tablet",
  "strength": "200mg",
  "prescription_only": true,
  "price": "14.71",
  "manufacturer_id": 2,
  "atc_code": "A10BA02"
}
```

**Response — 404 Not Found (unknown PZN)**

```json
{"detail":"medication '99999999' not found"}
```

---

### GET /v1/stats/dosage-forms

No parameters. Returns a count of medications for every dosage form present in the data — handy for a quick summary chart or a sanity check after loading a feed.

```bash
curl http://localhost:8000/v1/stats/dosage-forms
```

```python
import requests

r = requests.get("http://localhost:8000/v1/stats/dosage-forms")
for form, count in r.json().items():
    print(f"{form:10s} {count}")
```

```javascript
const counts = await (
  await fetch("http://localhost:8000/v1/stats/dosage-forms")
).json();

Object.entries(counts).forEach(([form, n]) => console.log(form, n));
```

**Response — 200 OK**

```json
{
  "tablet": 15,
  "capsule": 11,
  "solution": 9,
  "injection": 12,
  "cream": 11,
  "drops": 12,
  "spray": 10
}
```

---

### POST /v1/ask (optional)

Optional, only live if `DEEPSEEK_API_KEY` is set in `.env`. Ask a plain-language question; the service finds a handful of relevant medication rows and asks a language model (DeepSeek) to answer using only that data, citing the PZNs it used.

**Purpose**

Every other endpoint above requires you to already know how to query — a PZN, a dosage form, a search term. This one is a small demonstration of **retrieval-augmented generation**: turning the structured `medications` table into something you can ask a plain question and get a grounded, checkable answer from, instead of writing SQL yourself. "Grounded" is the operative word — the model is instructed to answer only from the rows it's handed, and every PZN it cites can be looked up with [`GET /v1/medications/{pzn}`](#get-v1medicationspzn) to verify the claim, rather than trusting the model's own memory of drug facts.

**How it works right now**

1. Your question is lowercased and split into words; short filler words ("the", "a", "what", "treats"...) are dropped, leaving the meaningful terms.
2. Each remaining term is matched, case-insensitively, against every medication's `name` and `active_ingredient` — up to 5 matching rows are pulled. If nothing matches any term, it falls back to the first 5 rows in the table (by PZN) so there's always *something* to answer from.
3. Those rows are formatted as a short plain-text list and sent to DeepSeek's `deepseek-chat` model (via the OpenAI-compatible endpoint `https://api.deepseek.com`) with an instruction to answer using only that list and cite the PZN(s) it used.
4. The model's reply is returned as `answer`. Separately, every candidate PZN that appears literally in the reply text is collected into `pzns_cited` — a simple string check, not something the model fills in itself.

> **Current limits, plainly stated:** matching is keyword-based, not semantic — a question with none of a medication's words in it won't find that medication, even if it's clearly related. It only ever looks at up to 5 rows. Each token is matched literally (special characters like `%`/`_` in your question can't be used to widen the match). And it's the one endpoint that isn't a pure database read: it makes an outbound call to DeepSeek and can take a few seconds. If the key isn't configured, it responds `503` immediately instead of hanging or timing out.

| Body field | Type | | Meaning |
|---|---|---|---|
| `question` | string | required | A natural-language question, sent as JSON in the request body (not a query param). |

```bash
curl -X POST http://localhost:8000/v1/ask \
  -H "Content-Type: application/json" \
  -d '{"question": "What tablets contain metformin?"}'
```

```python
import requests

r = requests.post(
    "http://localhost:8000/v1/ask",
    json={"question": "What tablets contain metformin?"},
)
data = r.json()
print(data["answer"])
print("cited PZNs:", data["pzns_cited"])
```

```javascript
const res = await fetch("http://localhost:8000/v1/ask", {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ question: "What tablets contain metformin?" }),
});
const data = await res.json();
console.log(data.answer, data.pzns_cited);
```

**Response — 200 OK (real output from this exact request)**

```json
{
  "answer": "PZN 00000001: Sanavia Metformin (Metformin, tablet, 200mg, prescription_only=True, price=14.71)",
  "pzns_cited": ["00000001"]
}
```

**Response — 503 (no key configured)**

```json
{"detail":"DEEPSEEK_API_KEY is not configured"}
```

---

## Error responses

Every endpoint returns plain JSON with a `detail` field on failure — check the status code first, then read `detail` for what went wrong. There is no separate "error format" to learn.

| Status | When | Body shape |
|---|---|---|
| `200` | Success. List/search endpoints return `200` even with zero matches. | the resource, or a list envelope |
| `404` | `GET /v1/medications/{pzn}` with a PZN that doesn't exist. | `{"detail": "medication '...' not found"}` |
| `422` | An invalid query param — an unrecognized `dosage_form`, or `limit` outside 1–500. | `{"detail": "..."}` |
| `503` | `/health` when the database is unreachable, or `/v1/ask` when no DeepSeek key is configured. | `{"detail": "..."}` |

**422 example — bad dosage_form**

```
$ curl "http://localhost:8000/v1/medications?dosage_form=lozenge"
{"detail":"dosage_form must be one of ['capsule', 'cream', 'drops', 'injection', 'solution', 'spray', 'tablet']"}
```

**422 example — limit out of range**

```
$ curl "http://localhost:8000/v1/medications?limit=999"
{"detail":[{"type":"less_than_equal","loc":["query","limit"],"msg":"Input should be less than or equal to 500", ...}]}
```

The two 422 shapes look different on purpose: `dosage_form` is checked by hand inside the route, while `limit`'s bound is enforced automatically by the framework before your code even runs — both are still plain `422`s, just worth knowing so a strict "detail is always a string" assumption doesn't break your error handling.

---

## A complete example

Putting it together: a small script with no dependencies beyond `requests` that pages through every tablet in the catalog and prints a running total.

```python
import requests

BASE_URL = "http://localhost:8000"

def all_tablets():
    """Yield every tablet-form medication, paging through the API."""
    offset = 0
    limit = 50
    while True:
        resp = requests.get(
            f"{BASE_URL}/v1/medications",
            params={"dosage_form": "tablet", "limit": limit, "offset": offset},
        )
        resp.raise_for_status()
        page = resp.json()

        yield from page["items"]

        offset += limit
        if offset >= page["total"]:
            break

if __name__ == "__main__":
    total_value = 0.0
    for med in all_tablets():
        total_value += float(med["price"])
        print(f"{med['pzn']}  {med['name']:<28} {med['price']:>8}")

    print(f"\n{'-' * 40}")
    print(f"combined price of all tablets: {total_value:.2f}")
```

> **The pattern to remember:** read `total` from the first response, keep bumping `offset` by `limit`, and stop once `offset` reaches `total`. Every list-shaped endpoint above follows that same loop.

---

*medref-pipeline · FastAPI service, port 8000 · interactive explorer at `/docs`*
