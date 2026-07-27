# LedgerLens

Drop in a photo of any receipt or invoice; get schema-validated structured
data, per-field confidence scores, and a review queue for the rows the
model isn't sure about — no custom parser per vendor, ever.

## What this is

A document-intelligence service built around one idea: **never silently
trust a model's output.** Every extracted field carries a confidence
score. Anything below threshold — including images that aren't a receipt
at all — goes to a human review queue instead of being auto-committed.
Every upload is moderation-screened before it ever reaches the vision
model. Every archived image is watermarked for provenance. Every log line
is scrubbed of PII. A reviewer can approve *or reject* anything sitting in
the queue, and both outcomes are tracked on a live Grafana dashboard.

---

## Architecture

```
Streamlit UI ──> FastAPI /ingest
                     │
                     ▼
              Moderation gate (OpenAI omni-moderation-latest, image input)
                     │ block → 422, vision model never called
                     ▼ allow
              GPT-4o vision extraction (Pydantic response_format = InvoiceSchema)
                     │  ── model refuses, or output fails schema validation
                     │     (e.g. empty date on a non-receipt image) ──┐
                     ▼                                                ▼
              Confidence router (< 0.75 → review queue)      zero-confidence fallback
                     │                                        ("Unrecognized document")
                     ▼                                                │
              PIL watermark → uploads/{doc_id}/watermarked.png <──────┘
                     │
                     ▼
              SQLite (documents, review_queue)
                     │
                     ▼
              Prometheus metrics ──> Grafana dashboard (auto-provisioned)

Reviewer flow: Streamlit Review Queue → GET /review → correct flagged
fields in st.data_editor → POST /approve (or POST /reject) → document
leaves the queue → outcome counted in Grafana ("Human Review Decisions")
```

**Design principle carried through every layer:** a failure to extract
cleanly is not an error condition — it's routed to a human, the same way
a genuinely low-confidence field is. A non-receipt image, a refusal from
the model, or output that fails our own Pydantic validation all collapse
into the same "send to Review Queue" path rather than three different
error states the operator has to reason about separately.

---

## Project layout

```
app/
  main.py              FastAPI app: /ingest /review /approve /reject
                        /documents/{id} /metrics /health
  schemas.py           Pydantic contracts (InvoiceSchema, LineItem,
                        ApproveRequest, RejectRequest, ...)
  moderation.py        Image moderation gate (OpenAI Moderation API)
  extraction.py        GPT-4o vision extraction bound to response_format,
                        with fallback handling for refusals and schema
                        validation failures
  review_router.py     Confidence-threshold routing logic
  watermark.py         PIL provenance watermarking
  pii.py               Regex PII redaction + logging.Filter
  db.py                SQLAlchemy models (documents, review_queue)
  metrics.py           Prometheus Counter/Histogram/Gauge definitions
  config.py            Thresholds, model names, cost table

streamlit_app/app.py   Upload + Review Queue UI (session-state-backed
                        navigation, st.data_editor for corrections)

grafana/
  dashboards/ledgerlens.json           Pre-built dashboard, 7 panels
  provisioning/datasources/            Auto-connects Prometheus
  provisioning/dashboards/             Auto-loads the dashboard above

tests/                 28 pytest tests covering schema contracts,
                        moderation routing, confidence routing, PII
                        redaction, extraction fallback paths, and the
                        approve/reject endpoints end-to-end

scripts/print_links.sh Polls every service's health endpoint from the
                        host and prints clickable links once ready

Makefile               docker compose wrapper: full-stack and
                        per-service start/stop/restart, plus reset-data
.github/workflows/deploy.yml   pytest gate -> docker build -> Cloud Run
Dockerfile
docker-compose.yml      api + streamlit + prometheus + grafana
prometheus.yml
```

---

## Setup

```bash
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
export OPENAI_API_KEY=sk-...
```

You'll need a funded OpenAI API key (pay-as-you-go, minimum $5) — GPT-4o
vision and the Moderation API both require billing to be active on the
account, even for small test volumes.

---

## Run locally (no Docker)

```bash
# API
uvicorn app.main:app --reload --port 8080

# Reviewer UI, separate terminal
LEDGERLENS_API=http://localhost:8080 streamlit run streamlit_app/app.py
```

---

## Run the full stack with Docker (recommended)

The `Makefile` wraps `docker compose`, waits for every service to report
genuinely healthy (not just "container started"), and prints clickable
links once everything is ready.

```bash
export OPENAI_API_KEY=sk-...
make up
```

```
============================================================
 LedgerLens is up
============================================================
  Streamlit UI (upload/review):  http://localhost:8511
  API docs (Swagger):            http://localhost:8090/docs
  API health check:              http://localhost:8090/health
  API raw metrics:                http://localhost:8090/metrics
  Prometheus targets:              http://localhost:9091/targets
  Grafana dashboard:               http://localhost:3001
    login: admin / admin
============================================================
```

Grafana comes up with the Prometheus data source and the full
"LedgerLens Observability" dashboard **already configured** — no manual
setup. Seven panels: documents ingested by status, auto-approval rate,
throughput, moderation latency (p95), extraction latency (p95),
cumulative extraction cost, and human review decisions (approved vs.
rejected).

### Common commands

| Command | Effect |
|---|---|
| `make up` | Build (if needed) + start everything + print links |
| `make down` | Stop and remove all containers (volumes persist) |
| `make restart` | `down` then `up` |
| `make rebuild` | Clean rebuild, no Docker layer cache |
| `make reset-data` | Wipe all documents/uploads/Grafana state for new/fresh start — dashboard auto-reprovisions |
| `make logs` | Tail logs from every service |
| `make ps` | Show container status |

### Controlling one service at a time

```bash
make stop-grafana        # stop just Grafana
make start-grafana       # bring it back
make restart-api         # rebuild + restart just the API after a code change
make restart-streamlit
make restart-prometheus
```
Equivalent raw `docker compose` commands work identically if preferred —
e.g. `docker compose logs -f api` for one service's logs.

### Without the Makefile

```bash
docker compose up --build
```
Same four services, same ports, just without the automatic health-wait
and link banner.

---

## Run tests

```bash
pytest tests/ -v
```

28 tests: schema round-tripping and validation, moderation routing
(including the "blocked images never reach the vision model" contract),
confidence-threshold routing, PII redaction, extraction fallback for
both outright refusals and schema-validation failures, and full
end-to-end approve/reject flows including the Prometheus counters they
drive. This is the exact suite the CI pipeline runs before every build.

---

## API

| Endpoint | Method | Purpose |
|---|---|---|
| `/ingest` | POST | Upload image → moderation → extraction → routing → storage |
| `/review` | GET | List documents pending human review, with flagged fields |
| `/approve` | POST | Apply reviewer corrections, mark document approved |
| `/reject` | POST | Reject a queued document, removes it from the queue |
| `/documents/{id}` | GET | Fetch a single document record |
| `/metrics` | GET | Prometheus scrape endpoint |
| `/health` | GET | Liveness check |

Interactive docs at `/docs` (Swagger UI) once the API is running.

---

## Continuous Integration

CI is wired in `.github/workflows/deploy.yml`, with two jobs:

1. **`test`** — installs dependencies and runs the full pytest suite
   (28 tests) on every push and pull request against `main`.
2. **`build-and-verify-stack`** — runs only if `test` passes. Mirrors the
   "Run the full stack with Docker" workflow above (the CI equivalent of
   `make up`): builds every image with `docker compose up --build -d`,
   then polls each service's health endpoint (API, Streamlit, Prometheus,
   Grafana) until all four report healthy, confirms `/metrics` is
   scrapeable, then tears everything down. This validates that the
   Dockerfile and `docker-compose.yml` actually produce a working stack
   on every push — no external cloud deployment involved.

Only repo secret needed: `OPENAI_API_KEY` (used so the API container has
a key available at boot; the CI job only exercises `/health` and
`/metrics`, not real `/ingest` calls, so a valid key isn't strictly
required for the smoke test to pass — but the container should still
build and start cleanly either way).

See `BUILD_NOTE.md` for what shipped, key decisions, and known
limitations.

See `VALIDATION_PLAN.md` for a full gap analysis against the original
brief plus root-cause analysis and a concrete fix plan for known issues
(Review Queue image loading, approve/reject visibility, Upload-tab state
handling, and a UI polish pass).
