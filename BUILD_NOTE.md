# Build Note — LedgerLens

## What shipped

The full core pipeline from the brief: a Streamlit upload UI sends each
image through an OpenAI Moderation gate *before* any vision-model call —
a blocked image never reaches GPT-4o. Clean images go through GPT-4o
vision, bound directly to a Pydantic `response_format` (`InvoiceSchema`),
so the SDK enforces the output shape rather than relying on manual JSON
parsing and retries. Every extracted field carries a per-field confidence
score; anything under 0.75 routes the document to a human review queue
instead of auto-committing it. Reviewers correct flagged fields in an
`st.data_editor` table and either approve or reject from the same panel
— both outcomes are tracked as separate Prometheus counters and
visualized on a Grafana dashboard that auto-provisions on container
startup, no manual setup. Every archived image gets a visible PIL
watermark before storage. PII (SSN/email/phone) is redacted from every
log line via a `logging.Filter`, so it can't be bypassed by a call site
forgetting to scrub. The whole stack is Dockerized with health checks,
and GitHub Actions runs the 34-test pytest suite as a gate before deploy.

## Key decisions

**Uncertainty routes to a human, uniformly.** The most consequential
decision was treating three different failure modes identically: a
genuinely low-confidence field, a model refusal, and model output that
fails our own Pydantic validation (e.g. an empty date on a non-receipt
image) all collapse into the same "send to Review Queue" path with a
zero-confidence stub, rather than three separate error states. This kept
the confidence router — the actual engineering point of the brief — as
the single source of truth for what needs human eyes.

**SQLite for storage**, matching the brief's own note that this is fine
for local/Docker-compose development. Deploying to Cloud Run would need
a GCS bucket for images and a persistent DB, since Cloud Run's
filesystem is ephemeral — documented below as a limitation rather than
solved, since the brief marks that as optional infra hardening.

**Reviewer UI navigation uses `st.radio`, not `st.tabs()`.** Streamlit's
native tabs widget has a still-open bug where `st.rerun()` — needed
after every Approve/Reject/Refresh action — resets the UI back to the
first tab. A radio-backed `session_state` value sidesteps this entirely.

## Core vs. stretch

Everything above is core, matching the brief's defined outcomes
one-for-one. No stretch goals (visual RAG search over archived invoices,
voice summaries, a LangGraph-based review workflow, or SSE streaming)
were attempted — they require the FastAPI → LangGraph → Cloud Run
reference material and sit outside this build's definition of done.

## Known limitations

- SQLite and the local `uploads/` directory don't survive a Cloud Run
  container restart or scale-out event — solid for local/Docker-compose
  (where `DATABASE_URL` now correctly points into the persisted
  `ledgerlens_db` volume, so a `make restart-api` no longer wipes it),
  but Cloud Run's ephemeral filesystem still means production persistence
  there is a follow-up (a GCS bucket for images + Cloud SQL or a
  persisted SQLite file), not silently glossed over.
- Rolling metrics (`throughput_docs_per_minute`, `auto_approval_rate`)
  are computed in-memory over a bounded deque — correct for a single
  replica; multi-replica deployment would need Redis or Prometheus
  queries instead.
- CI (`.github/workflows/deploy.yml`) builds and starts the full Docker
  Compose stack on every push as a smoke test, but doesn't deploy
  anywhere external — there's no live cloud environment for this build.
  Deploying to Cloud Run, Render, or Railway is documented as the next
  step but wasn't executed from the development environment used here.
- A live `gcloud run deploy` was not executed from the development
  environment (no GCP credentials there); the Dockerfile and workflow
  file are written and locally verified to build correctly, but the
  actual cloud deploy needs real credentials to confirm end-to-end.

See `VALIDATION_PLAN.md` for the full list of issues found in a later
audit pass (Review Queue image loading, approve/reject visibility,
Upload-tab state handling, the nested-line-item-correction gap, and the
SQLite volume mismatch above) and what was done about each.
