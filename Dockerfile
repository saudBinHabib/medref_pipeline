FROM python:3.11-slim

WORKDIR /app

COPY pyproject.toml ./
COPY src ./src

RUN pip install --no-cache-dir .

# postgresql-client (psql) and the migrations directory are needed for the
# one-off medref-migrate task (CMD override), which shares this same image
# with the API and pipeline task defs.
RUN apt-get update && apt-get install -y --no-install-recommends postgresql-client \
    && rm -rf /var/lib/apt/lists/*
COPY migrations ./migrations

# data/atc_reference.csv is read at a relative path by
# src/enrich.py::load_atc_reference_csv() -- needed by the pipeline task.
COPY data ./data

CMD ["uvicorn", "src.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
