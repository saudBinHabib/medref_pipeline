FROM python:3.11-slim

WORKDIR /app

COPY pyproject.toml ./
COPY src ./src

RUN pip install --no-cache-dir .

# api/main.py does not exist yet (scaffolding stage) — this entrypoint
# becomes runnable once SPEC.md §6 (src/api/main.py) is implemented.
CMD ["uvicorn", "src.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
