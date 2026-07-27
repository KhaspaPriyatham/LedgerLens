# LedgerLens — Validation & Fix Plan

**Status:** Draft for review · **Scope:** Gap analysis against the IITR-SE-2509 Cohort C · C·02 "LedgerLens" capstone brief, plus root-cause analysis and a concrete fix plan for four reported defects and one requested UX pass.
**Audience:** Anyone picking up this repo cold — every finding below cites the exact file/line it comes from and includes a copy-pasteable fix.

---

## 1. How to use this document

Each finding below follows the same shape:

- **What the brief/user asked for**
- **What the repo actually does today** (file + line reference)
- **Root cause** (why it's broken, not just that it is)
- **Fix** (concrete code change, ready to implement)
- **How to verify**

Section 4 is the exhaustive brief-vs-repo compliance matrix. Section 5 covers the four bugs + UI request the user reported, in the order given. Section 6 covers gaps found during the audit that were *not* explicitly reported but are real. Section 7 is the prioritized task list an implementer should actually follow.

---

## 2. TL;DR

| # | Item | Status | Severity |
|---|---|---|---|
| 1 | Review Queue image doesn't load | **Confirmed bug** — architectural, not cosmetic | High |
| 2 | Approve/Reject doesn't visibly move docs to an accepted/rejected list | **Partially true** — backend is correct, UI surface is missing | Medium |
| 3 | Upload tab image clears itself before user clicks Clear | **Confirmed bug** — reproducible from source | High |
| 4 | UI should be fancier | Valid ask, no blocker — design plan below | Low (cosmetic) |
| 5 | SQLite persistence / Docker volume mismatch | **Newly found bug**, not reported by user | High (data loss) |
| 6 | Nested line-item corrections not written back | Known limitation (already documented in `BUILD_NOTE.md`), still open | Medium |
| 7 | No live Cloud Run deploy from CI | Documented limitation, brief allows the alternative (local run steps) | Low/Optional |
| 8 | No document list/browse endpoint | Needed to build fix for #2 | — |

Everything else the brief asks for as a **Core Outcome** is already implemented correctly (see §4). This is a strong build — the four reported issues are real, narrow, and each has one clear root cause; they are not symptoms of a deeper design problem.

---

## 3. Brief recap (for context)

LedgerLens (`C·02`, Document Intelligence track) core outcomes, verbatim from the brief:

1. Upload a receipt/invoice image via Streamlit; image passes an OpenAI Moderation gate before any LLM call.
2. GPT-4o vision extracts structured fields validated against a Pydantic schema via `response_format`.
3. Every field carries a `confidence` float; fields below threshold (default 0.75) go to a human-review queue, not silently accepted.
4. A reviewer UI shows flagged rows in-line, lets the reviewer correct/approve; approved records are written to storage.
5. Stored source images are watermarked (PIL, visible) before archival.
6. FastAPI exposes `/ingest`, `/review`, `/approve` (Pydantic validated throughout).
7. Containerized (Docker), deployed to Cloud Run via GitHub Actions CI/CD running pytest schema-contract tests before deploy.
8. Prometheus + Grafana dashboard: per-document token cost, moderation latency, extraction latency, auto-approval rate, throughput.
9. PII (SSN/email/phone) redacted via regex before any log line is written.

The user's own enhancements beyond the brief: a `/reject` endpoint (paired with `/approve`) and a `Makefile` wrapping `docker compose` for one-command up/down/restart/cleanup of the whole stack or any single service.

---

## 4. Compliance matrix — Core Outcomes vs. repo

| Brief requirement | Implemented? | Evidence |
|---|---|---|
| Streamlit upload, moderation gate before LLM call | ✅ Yes | `app/main.py:68-99` calls `screen_image()` before `extract_invoice()`; blocked images never reach the vision model (asserted by `tests/test_moderation_gate.py:48-74`) |
| GPT-4o vision + Pydantic `response_format` | ✅ Yes | `app/extraction.py:84-97` uses `client.beta.chat.completions.parse(..., response_format=InvoiceSchema)` — the modern, correct SDK call for structured output (an improvement over the brief's `chat.completions.create` example) |
| Per-field confidence + review-queue routing | ✅ Yes | `app/schemas.py:59-72` (`low_confidence_fields`), `app/review_router.py:12-20` |
| Reviewer UI: view/correct/approve flagged fields | ⚠️ Partial | `streamlit_app/app.py:218-260` renders `st.data_editor`, posts to `/approve`. **Gap:** corrections to nested line-item fields are logged but not applied back into `line_items` (see §6.2) |
| Watermarking before archival | ✅ Yes | `app/watermark.py`, called unconditionally in `app/main.py:115` regardless of extraction outcome |
| FastAPI `/ingest /review /approve` + Pydantic validation | ✅ Yes, plus `/reject` (user's own addition) | `app/main.py`, `app/schemas.py` |
| Dockerized, Cloud Run + GH Actions CI/CD with pytest gate | ⚠️ Partial | Docker ✅ (`Dockerfile`, `docker-compose.yml`). CI ✅ runs the 28-test pytest suite (`.github/workflows/deploy.yml:10-27`) as a gate. **Gap:** the second CI job only builds and smoke-tests the Compose stack in the runner and tears it down (`deploy.yml:29-73`) — there is **no actual `gcloud run deploy` step**. The brief explicitly allows "if you didn't deploy, include clear run-it-locally steps instead" (brief pg. 2) and those steps are documented in `README.md`, so this is compliant-by-alternative, not a defect — flagged as optional follow-up in §6.3 |
| Prometheus + Grafana: cost, moderation latency, extraction latency, auto-approval rate, throughput | ✅ Yes, exceeds spec | `app/metrics.py` defines all 5 required metrics; `grafana/dashboards/ledgerlens.json` ships 7 panels, including a bonus "Human Review Decisions (Approved vs Rejected)" panel not required by the brief but matching the user's own `/reject` enhancement |
| PII redaction via regex before logging | ✅ Yes | `app/pii.py` — implemented as a `logging.Filter` attached at the logger level (`configure_redacted_logging`), so it can't be bypassed by a call site forgetting to scrub manually. This is a better pattern than the brief's minimum ask (scrub-at-call-site) |

**Verdict:** every mandatory Core Outcome is implemented. The gaps that exist are the ones covered in §§5–6 below, plus the optional stretch goals the brief explicitly marks as not required (Visual RAG search, voice summary, LangGraph review workflow, SSE streaming, batch mode) — none of which are attempted, and none of which need to be for a compliant submission.

---

## 5. Reported issues — root cause and fix

### 5.1 Issue 1 — Review Queue image doesn't load

**Symptom reported:** uploaded image should be visible on the Review Queue tab; it isn't.

**Current implementation:** `streamlit_app/app.py:158-187`. For each queued document, the UI takes `doc["image_path"]` (an **absolute filesystem path** returned verbatim from the API, e.g. `/app/uploads/<doc_id>/watermarked.png`) and tries to `os.path.exists()` + `open()` it **directly from the Streamlit process's own filesystem**. There's already a defensive v2 rewrite in place (explicit existence check, forced `.load()` decode, path/byte-count diagnostics on failure) — so if this is failing, it is not failing silently; it should be surfacing one of:
- `"No source image path recorded for this document."`
- `"Source image file not found at: <path>"`
- `"Could not load source image: <ExceptionType>: <msg>"`

**Root cause:** the architecture assumes the API container/process and the Streamlit container/process **share a filesystem**, and only works if that assumption holds:

- Under `docker compose` (the `Makefile`'s `make up`), it happens to hold *today* because both `api` and `streamlit` mount the same named volume at the same path (`docker-compose.yml:10-11` and `:35-36`) — `ledgerlens_uploads:/app/uploads` (rw) and `ledgerlens_uploads:/app/uploads:ro` respectively. If that's the exact setup being run, and the volume was never manually reset/renamed between compose file edits, images *should* resolve.
- The assumption **breaks** in several realistic situations this project already anticipates or will hit:
  1. **Local dev without Docker** per the README's own documented "Run locally (no Docker)" path — if API and Streamlit are ever run as genuinely separate machines/containers/sandboxes (e.g. a teammate's machine, a remote dev container, a future split deployment), the absolute path from one process means nothing on the other.
  2. **Cloud Run**, which the brief explicitly targets for deployment: *"Cloud Run has an ephemeral local filesystem... store images in a GCS bucket... "* (brief pg. 8, Storage row). Two separate Cloud Run services (API, Streamlit) **cannot share a local disk at all** — this design will not work post-deploy even if it works in Docker Compose today.
  3. Any accidental stale/mismatched named volume from a prior compose config change (Docker preserves named volumes across `docker compose up` unless `-v`/`down -v` is used) silently produces "file not found" for otherwise-valid documents.

In short: **cross-container filesystem coupling is the root cause**, whether or not it happens to work in the exact environment the bug was noticed in. This needs an architectural fix, not a path tweak.

**Fix — serve images through the API, don't read them off disk from Streamlit:**

Add an image-serving endpoint to `app/main.py`:

```python
from fastapi.responses import FileResponse
import os

@app.get("/documents/{document_id}/image")
def get_document_image(document_id: str, db: Session = Depends(get_db)):
    doc = db.query(Document).filter(Document.id == document_id).first()
    if not doc or not doc.image_path or not os.path.exists(doc.image_path):
        raise HTTPException(status_code=404, detail="Image not found for this document")
    return FileResponse(doc.image_path, media_type="image/png")
```

Update `streamlit_app/app.py`'s Review Queue image block (currently lines 158-187) to fetch bytes over HTTP instead of touching the local filesystem:

```python
img_resp = requests.get(f"{API_BASE}/documents/{doc['document_id']}/image", timeout=15)
if img_resp.status_code == 200:
    st.image(img_resp.content, caption="Watermarked source", width=300)
else:
    st.warning(f"Source image not available (HTTP {img_resp.status_code}).")
```

This is correct under Docker Compose (still works, no volume needed at all now), correct for local no-Docker dev (works over `http://localhost:8080`), and correct if API and Streamlit are later deployed to separate Cloud Run services — it removes the shared-filesystem assumption entirely, which is the only fix that's actually future-proof given the brief's own deployment target.

Once this lands, the `ledgerlens_uploads:/app/uploads:ro` mount on the `streamlit` service in `docker-compose.yml` (lines 35-36) is no longer needed and can be removed to keep the compose file honest about what each service actually depends on.

**Verify:** upload a receipt, let it land in Review Queue (or force it there — see `tests/test_reject_endpoint.py:30-40` for the exact "low-confidence invoice" fixture pattern), confirm the thumbnail renders. Add a test: `GET /documents/{id}/image` on a real ingested doc returns 200 with `content-type: image/png` and non-empty bytes; on an unknown id returns 404.

---

### 5.2 Issue 2 — Approve/Reject should clear the queue and file into accepted/rejected lists, visible in Grafana

**Symptom reported:** approving/rejecting a document should remove it from Review Queue and store it in an accepted/rejected list; this should also show in Grafana.

**What's already correct (no fix needed here):**
- `/approve` (`app/main.py:215-248`) sets `doc.status = "approved"`.
- `/reject` (`app/main.py:251-277`) sets `doc.status = "rejected"`.
- `GET /review` (`app/main.py:163-192`) only ever returns `Document.status == "pending_review"` rows — so both approve and reject **do** remove the document from the queue immediately. This is asserted end-to-end by `tests/test_reject_endpoint.py:64-66` and `tests/test_approve_metric.py:53-55`, and both tests pass against the current code.
- `documents_reviewed_total{outcome="approved"|"rejected"}` (`app/metrics.py:30-35`) is incremented on both paths, and Grafana panel 7, *"Human Review Decisions (Approved vs Rejected)"* (`grafana/dashboards/ledgerlens.json:102-122`), already charts exactly this, green vs. red, auto-provisioned on container start.

**The actual gap:** there is no way to *see* the accepted/rejected lists anywhere in the product. The data is correctly persisted (`status="approved"`/`"rejected"` rows exist in the `documents` table) and correctly counted (Grafana), but:
- `app/main.py` has no list endpoint — only `GET /documents/{document_id}` (singular, `:195-212`). There is no `GET /documents?status=...`.
- `streamlit_app/app.py` has exactly two nav destinations (`:39` — `"📤 Upload"`, `"🕵️ Review Queue"`). There is no "Accepted" or "Rejected" (or combined "Processed Documents") view.

So from the outside, approving/rejecting *looks* like the document vanishes into the void — which reads exactly like "it isn't happening," even though the state transition and the Grafana counters are both working correctly underneath. This is the actual thing to build.

**Fix:**

1. Add a list endpoint to `app/main.py`:

```python
@app.get("/documents")
def list_documents(status: str | None = None, limit: int = 50, db: Session = Depends(get_db)):
    q = db.query(Document)
    if status:
        q = q.filter(Document.status == status)
    docs = q.order_by(Document.created_at.desc()).limit(limit).all()
    return [
        {
            "document_id": d.id,
            "filename": d.filename,
            "status": d.status,
            "vendor": d.vendor,
            "total": d.total,
            "currency": d.currency,
            "created_at": d.created_at.isoformat() if d.created_at else None,
        }
        for d in docs
    ]
```
   (Register this route **above** `@app.get("/documents/{document_id}")` if using a single router — FastAPI matches path operations in declaration order, and `/documents` vs `/documents/{document_id}` won't collide since one has no path segment after `documents`, but keep them adjacent for readability regardless.)

2. Extend the Streamlit nav (`streamlit_app/app.py:37-44`) with an "✅ Accepted" and "❌ Rejected" destination (or one combined "📋 Processed" tab with a status `st.selectbox`), each calling `GET /documents?status=approved` / `?status=rejected`, rendering a table (`st.dataframe`) with vendor/total/date/status, and an expander per row showing the extracted JSON + image (via the new `/documents/{id}/image` endpoint from §5.1) + `reviewed_json` (the reviewer's applied corrections) for auditability.

3. No Grafana changes are required — panel 7 already covers this. Once the SQLite persistence bug in §6.1 is fixed, the whole loop (approve/reject → list view → Grafana panel) will be consistent across restarts too.

**Verify:** approve one document and reject another; confirm both disappear from Review Queue (already covered by existing tests) **and** now appear in the new Accepted/Rejected views; confirm the Grafana "Human Review Decisions" panel increments for each.

---

### 5.3 Issue 3 — Upload tab image clears itself before the user clicks Clear

**Symptom reported:** when an unsupported document or a low-confidence extraction result comes back, the previewed image disappears from the Upload tab on its own; it should stay until the user explicitly clicks "Clear / upload a different file."

**Root cause — confirmed in source, two contributing bugs:**

**(a) Explicit state-clearing on error responses.** `streamlit_app/app.py:95-107`:

```python
if resp is not None:
    if resp.status_code == 422:
        detail = resp.json().get("detail", {})
        st.error(f"🚫 Blocked by moderation: {detail.get('blocked_reason')}")
        st.session_state.last_extraction = None
        st.session_state.last_extraction_image = None          # <-- clears the preview
    elif resp.status_code == 200:
        st.session_state.last_extraction = resp.json()
        st.session_state.last_extraction_image = uploaded.getvalue()
    else:
        st.error(f"Unexpected error: {resp.status_code} — {resp.text}")
        st.session_state.last_extraction = None
        st.session_state.last_extraction_image = None          # <-- clears the preview
```

A moderation-blocked upload (422 — "unsupported" content) and any unexpected API error (the `else` branch — also reachable for a genuinely unsupported/corrupt file that fails moderation or extraction with a 502) both explicitly null out `last_extraction_image`, which is the only thing keeping the preview on screen once the live widget value is gone (see (b) below). This is the direct cause of the "unsupported" half of the complaint.

Note the **200 branch is already correct** — a low-confidence "Unrecognized document" result still returns HTTP 200 (`status="pending_review"`), so `last_extraction_image` is *not* cleared on that path today. If low-confidence extractions are still visibly disappearing, (b) below is the reason, and it affects every outcome, not just the blocked one.

**(b) `st.file_uploader`'s value is not reliably sticky across reruns, and this app treats it as the primary source of truth for the preview.** Line 72:

```python
preview_bytes = uploaded.getvalue() if uploaded is not None else st.session_state.last_extraction_image
```

Per Streamlit's own documented widget lifecycle, a widget's value in `st.session_state` is deleted whenever that widget is **not instantiated** during a script rerun. Because Upload and Review Queue are two mutually-exclusive `if active_tab == "Upload": ... else: ...` branches (`streamlit_app/app.py:67` / `:138`), the `st.file_uploader` call only executes while the user is actually looking at the Upload tab. Switching to Review Queue and back — or any other rerun that happens while the user isn't on Upload — causes Streamlit to drop the uploader's stored file, so `uploaded` comes back `None` on return. The line above then falls through to `last_extraction_image`, which is correct *only as long as that value was never nulled* — which directly compounds bug (a): the moment a blocked/error response has run once, there is nothing left to fall back to, ever, even after re-navigating.

**Fix — make `last_extraction_image` (and a new `last_error`) the single source of truth, and never null the image on error:**

```python
if uploaded is not None:
    st.session_state.last_extraction_image = uploaded.getvalue()
preview_bytes = st.session_state.last_extraction_image
```

And in the response-handling block, stop clearing the image on failure paths — only track the error message itself in session_state so it also survives reruns instead of flashing once:

```python
if resp is not None:
    if resp.status_code == 422:
        detail = resp.json().get("detail", {})
        st.session_state.last_error = f"🚫 Blocked by moderation: {detail.get('blocked_reason')}"
        st.session_state.last_extraction = None
        # last_extraction_image intentionally left untouched — preview persists
    elif resp.status_code == 200:
        st.session_state.last_extraction = resp.json()
        st.session_state.last_error = None
    else:
        st.session_state.last_error = f"Unexpected error: {resp.status_code} — {resp.text}"
        st.session_state.last_extraction = None
```

And render `st.session_state.last_error` (if set) near the top of the results section, persistently, instead of only inside the button-click branch. The "Clear / upload a different file" handler (`:80-84`) already correctly resets everything including bumping `uploader_generation` — that logic doesn't need to change, it's the *only* place these should be reset.

**Verify:** upload a receipt that gets blocked by moderation (or force a 422 in a test harness) — confirm the preview image stays visible with the error message, survives switching to Review Queue and back, and only disappears after clicking Clear. Repeat for a low-confidence/"Unrecognized document" result and for a simulated 502.

---

### 5.4 Issue 4 — Make the UI fancier

This is a design pass, not a bug fix. Concrete, dependency-free plan (everything below uses only Streamlit + CSS/HTML already available via `st.markdown(..., unsafe_allow_html=True)` — no new packages needed in `requirements.txt`):

1. **Theme file** — add `.streamlit/config.toml`:
   ```toml
   [theme]
   primaryColor = "#4F8BF9"
   backgroundColor = "#0E1117"
   secondaryBackgroundColor = "#1B1F27"
   textColor = "#FAFAFA"
   font = "sans serif"
   ```
   (Or a light-mode palette — pick one and keep it consistent; Streamlit respects this automatically, no code changes needed elsewhere.)

2. **Status badge helper** — a small shared function (e.g. in a new `streamlit_app/ui.py`) rendering a colored HTML pill instead of plain text for `status`:
   ```python
   STATUS_COLORS = {
       "auto_approved": "#2ecc71", "approved": "#2ecc71",
       "pending_review": "#f1c40f", "blocked": "#e74c3c", "rejected": "#e74c3c",
   }
   def status_badge(status: str) -> str:
       color = STATUS_COLORS.get(status, "#95a5a6")
       return f'<span style="background:{color};color:#111;padding:2px 10px;border-radius:12px;font-size:0.85em;font-weight:600">{status}</span>'
   ```
   Use via `st.markdown(status_badge(doc["status"]), unsafe_allow_html=True)` anywhere a status is shown (Upload result, Review Queue cards, Accepted/Rejected lists from §5.2).

3. **KPI row** at the top of every tab — `st.columns(5)` + `st.metric` fed from `GET /documents` (§5.2) grouped by status, plus `token_cost_usd_total` from `/metrics`: Total documents, Pending Review, Auto-Approved, Approved, Rejected, Total extraction cost.

4. **Card layout for Review Queue / Accepted / Rejected** — replace the bare `st.expander` per document with a bordered container (`st.container(border=True)`, supported in current Streamlit versions) holding: thumbnail, status badge, vendor/total/date header line, and the existing detail content unchanged.

5. **Branded header** — replace the plain `st.title` with a header banner (`st.markdown` block: title + one-line subtitle + a small nav/status strip), and a sidebar (`st.sidebar`) with quick links to Grafana / Swagger docs / Prometheus (the same links `scripts/print_links.sh` already prints to the terminal — surface them in-app too) plus a live "API health" indicator (`GET /health`).

6. Keep all of this additive — none of it touches the ingest/review/approve/reject request logic, so it can be built and reviewed independently of §5.1–5.3.

**Verify:** visual review only — no automated test required, but a quick smoke pass (`make up`, click through Upload → blocked → pending_review → approve/reject → Accepted/Rejected) confirms nothing regressed functionally while restyled.

---

## 6. Additional gaps found during the audit (not explicitly reported)

### 6.1 SQLite / uploads volume mismatch in `docker-compose.yml` — data-loss bug

`docker-compose.yml:6-12`:
```yaml
api:
  environment:
    - DATABASE_URL=sqlite:////app/ledgerlens.db     # <- DB file lives here
  volumes:
    - ledgerlens_uploads:/app/uploads
    - ledgerlens_db:/app/db                          # <- but the persisted volume is mounted here
```
The `ledgerlens_db` named volume is mounted at `/app/db`, but `DATABASE_URL` points the SQLite file at `/app/ledgerlens.db` — a different path, on the container's own ephemeral writable layer, not inside the volume at all. Net effect: **every `documents` and `review_queue` row is lost whenever the `api` container is recreated** — including via the Makefile's own `make restart-api` (`docker compose up -d --build api`), one of the exact convenience commands the user added as an enhancement. The `uploads/` files *are* correctly persisted (that volume mount is right), so a restart leaves orphaned image files on disk with no DB rows pointing to them, while all review-queue/approved/rejected history disappears.

**Fix:**
```yaml
environment:
  - DATABASE_URL=sqlite:////app/db/ledgerlens.db
volumes:
  - ledgerlens_uploads:/app/uploads
  - ledgerlens_db:/app/db
```
(No Dockerfile change needed — SQLAlchemy/sqlite3 create the file on first write as long as the parent directory exists, and `/app/db` will exist because Docker creates the mount point.)

**Verify:** `make up` → ingest a document → `make restart-api` → `GET /documents` (§5.2) or the Review Queue should still show it. Currently this will fail; after the fix it will pass.

### 6.2 Nested line-item corrections aren't applied to structured data

Already self-documented as a known limitation in `BUILD_NOTE.md:56-60` and `:70-73`. `app/main.py:224-233` (`/approve`) only rewrites top-level fields (`if "." not in update.field_path and update.field_path in record`); dotted paths like `line_items[0].Widget` are appended to a `_reviewer_corrections` audit array but never rewritten into `record["line_items"][0]`.

This is real and worth closing, but lower priority than §5's items since it's already disclosed rather than silently broken. Suggested fix path: change `InvoiceSchema.low_confidence_fields()` (`app/schemas.py:59-72`) to flag the specific sub-field name (e.g. `line_items[0].amount`) rather than just the line description, so `/approve` can parse the index + sub-field and write `record["line_items"][idx][subfield] = corrected_value` directly, in addition to the audit trail entry (keep both — audit trail for provenance, structured write for correctness).

### 6.3 No live Cloud Run deploy step

`deploy.yml` stops at "build + smoke-test the Compose stack, then tear down" (`:29-73`) — there's no `gcloud run deploy`. This is brief-compliant as-is (local-run steps are documented and count as the alternative per the brief), but if the user wants the "Live deployment" checklist item from the submission requirements (brief pg. 2) satisfied literally, add a third job gated on `secrets.GCP_PROJECT_ID`/`secrets.GCP_SA_KEY` existing, running `gcloud run deploy` against the already-built image. Treat as optional/stretch, not a defect.

### 6.4 `/documents` list endpoint missing

Covered as the concrete fix for §5.2 — noted here again only so it isn't missed as "just a UI issue"; it's a real backend gap (needed regardless of how the frontend ends up displaying it).

### 6.5 Batch mode not implemented

Listed under the brief's "Sample features to build" (not the "Core outcomes" bullet list, and not "Open-ended stretch" either — it sits in the recommended-but-not-mandatory middle tier). Not implemented. Optional; only worth doing if the user wants extra polish beyond the graded core.

---

## 7. Prioritized implementation plan

| Phase | Task | Files touched | Depends on |
|---|---|---|---|
| **P0 — data integrity** | Fix SQLite volume/path mismatch (§6.1) | `docker-compose.yml` | — |
| **P1 — the 3 reported bugs** | Add `GET /documents/{id}/image`; switch Review Queue image loading to HTTP (§5.1) | `app/main.py`, `streamlit_app/app.py` | — |
| | Fix Upload-tab premature clearing: stop nulling `last_extraction_image` on error, make it the sticky source of truth, persist `last_error` (§5.3) | `streamlit_app/app.py` | — |
| | Add `GET /documents` list endpoint + Accepted/Rejected views in Streamlit (§5.2) | `app/main.py`, `streamlit_app/app.py` | P1's image-serving endpoint (for thumbnails in the new views) |
| **P2 — UI polish** | Theme file, status badges, KPI row, card layout, sidebar (§5.4) | `.streamlit/config.toml` (new), `streamlit_app/app.py`, optional new `streamlit_app/ui.py` | P1 (badges/cards are used across Upload, Review Queue, and the new Accepted/Rejected views) |
| **P3 — disclosed gaps, optional** | Nested line-item corrections written to structured data (§6.2) | `app/schemas.py`, `app/main.py` | — |
| | Cloud Run deploy job in CI (§6.3) | `.github/workflows/deploy.yml` | Requires GCP credentials as repo secrets — a decision the user needs to make, not purely an engineering task |
| | Batch mode (§6.5) | new endpoint + Streamlit page | — |

Recommended order: **P0 → P1 → P2 → P3**, since P0 prevents demo data from silently vanishing during exactly the kind of iterative testing this fix work requires, and P1 is the three things explicitly reported as broken.

---

## 8. New/updated tests to add

- `tests/test_document_image_endpoint.py` — `GET /documents/{id}/image` returns 200 + correct content-type for an ingested doc; 404 for an unknown id.
- `tests/test_documents_list_endpoint.py` — `GET /documents` returns documents; `GET /documents?status=approved` / `?status=rejected` filter correctly after calling `/approve` / `/reject`.
- `tests/test_db_persistence.py` (or a Docker-level smoke test, not pure pytest) — verifies the fixed `docker-compose.yml` actually persists a row across a container recreate. Pure pytest can't exercise Docker volumes directly; add this as a documented manual/CI smoke step instead (extend `.github/workflows/deploy.yml`'s existing `build-and-verify-stack` job to `docker compose restart api` mid-run and re-check a previously-ingested document via `curl`).
- Streamlit-side logic (session-state handling) isn't currently under any test — it's UI glue, consistent with the rest of the repo's testing strategy (all 28 existing tests are backend-only). No new Streamlit tests are proposed here to stay consistent with that existing scope decision; the fix is verified manually per §5.3.

## 9. Manual QA checklist (run after implementing §5 + §6.1)

1. `make up` (or `docker compose up --build -d`).
2. Upload a real receipt/invoice photo → confirm it either auto-approves or lands in Review Queue.
3. Open Review Queue → confirm the thumbnail image renders (§5.1 fix).
4. Approve one item with a field correction, reject another → confirm both disappear from Review Queue immediately, and both now appear in the new Accepted/Rejected views (§5.2 fix) with the correction reflected.
5. Open Grafana → confirm "Human Review Decisions" panel shows 1 approved + 1 rejected.
6. Upload a non-receipt image (a random photo) → confirm it lands in Review Queue as "Unrecognized document" and the **preview image stays visible** — switch to Review Queue tab and back to Upload, confirm it's still there (§5.3 fix).
7. Upload something that trips moderation (or simulate a 422 in a local harness) → confirm the error message and the preview both persist until "Clear / upload a different file" is clicked, not before.
8. `make restart-api` → re-check Review Queue / Accepted / Rejected still show prior documents (§6.1 fix).
9. Visual pass on the new theme/badges/KPI row/card layout (§5.4) — no functional assertions, just confirm nothing broke.

---

## 10. Appendix — file inventory (for orientation)

```
app/
  main.py              FastAPI app. Endpoints today: /health /metrics /ingest
                        /review /documents/{id} /approve /reject.
                        Plan adds: /documents (list), /documents/{id}/image.
  schemas.py            Pydantic contracts (InvoiceSchema, LineItem, Approve/RejectRequest, ...).
                        Plan touches: low_confidence_fields() to flag sub-field, not whole line item (§6.2).
  moderation.py         Image moderation gate — no changes needed.
  extraction.py         GPT-4o vision extraction — no changes needed.
  review_router.py      Confidence-threshold routing — no changes needed.
  watermark.py          PIL provenance watermarking — no changes needed.
  pii.py                Regex PII redaction — no changes needed.
  db.py                 SQLAlchemy models — no changes needed (fix is in docker-compose.yml, not here).
  config.py             Thresholds, model names, cost table — no changes needed.

streamlit_app/app.py    Plan touches heavily: image loading (§5.1), session-state handling (§5.3),
                        new nav destinations (§5.2), theming/badges/layout (§5.4).

docker-compose.yml      Plan touches: DATABASE_URL path (§6.1), optionally drop the now-unused
                         uploads:ro mount on the streamlit service once §5.1 lands.

.github/workflows/deploy.yml   Optionally extend with a real Cloud Run deploy job (§6.3, optional).
```

This document should be updated as each phase lands — treat the checkboxes implicit in §7 as the actual tracker.
