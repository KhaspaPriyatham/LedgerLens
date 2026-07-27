import json
import os

import pandas as pd
import requests
import streamlit as st

API_BASE = os.getenv("LEDGERLENS_API", "http://localhost:8000")
# Separate, browser-facing convenience links for the sidebar. API_BASE is
# often an internal address this process uses to reach the API (e.g. the
# Docker network name "http://api:8080"), which isn't reachable from the
# user's own browser -- these default to the host ports docker-compose.yml
# actually exposes, matching what `scripts/print_links.sh` prints.
API_DOCS_URL = os.getenv("LEDGERLENS_API_PUBLIC_URL", "http://localhost:8090")
GRAFANA_URL = os.getenv("GRAFANA_URL", "http://localhost:3001")
PROMETHEUS_URL = os.getenv("PROMETHEUS_URL", "http://localhost:9091")

st.set_page_config(page_title="LedgerLens", page_icon="📄", layout="wide")

STATUS_COLORS = {
    "auto_approved": "#2ecc71",
    "approved": "#2ecc71",
    "pending_review": "#f1c40f",
    "blocked": "#e74c3c",
    "rejected": "#e74c3c",
    "processing": "#95a5a6",
}
STATUS_LABELS = {
    "auto_approved": "auto-approved",
    "approved": "approved",
    "pending_review": "pending review",
    "blocked": "blocked",
    "rejected": "rejected",
    "processing": "processing",
}

st.markdown(
    """
    <style>
    .ll-badge {
        display: inline-block;
        padding: 2px 12px;
        border-radius: 999px;
        font-size: 0.82em;
        font-weight: 600;
        color: #111111;
        white-space: nowrap;
    }
    .ll-subtitle {
        opacity: 0.7;
        font-size: 0.95em;
        margin-top: -0.6rem;
        margin-bottom: 1rem;
    }
    .ll-health-dot {
        height: 10px;
        width: 10px;
        border-radius: 50%;
        display: inline-block;
        margin-right: 6px;
    }
    </style>
    """,
    unsafe_allow_html=True,
)


def status_badge(status: str) -> str:
    color = STATUS_COLORS.get(status, "#95a5a6")
    label = STATUS_LABELS.get(status, status)
    return f'<span class="ll-badge" style="background:{color};">{label}</span>'


def fetch_health() -> bool:
    try:
        resp = requests.get(f"{API_BASE}/health", timeout=5)
        return resp.status_code == 200
    except requests.RequestException:
        return False


def fetch_documents(status: str | None = None, limit: int = 200):
    """GET /documents, optionally filtered by status. Backs the KPI row and
    the Accepted / Rejected views."""
    try:
        params = {"limit": limit}
        if status:
            params["status"] = status
        resp = requests.get(f"{API_BASE}/documents", params=params, timeout=30)
        return resp.json() if resp.status_code == 200 else []
    except requests.RequestException:
        return []


def fetch_review_queue():
    try:
        resp = requests.get(f"{API_BASE}/review", timeout=30)
        return resp.json() if resp.status_code == 200 else []
    except requests.RequestException as exc:
        st.error(f"Could not reach API at {API_BASE}: {exc}")
        return []


def fetch_document_image(document_id: str):
    """Fetch the watermarked source image over HTTP via GET
    /documents/{id}/image, rather than reading a local filesystem path.
    This is what actually makes images render in the Review Queue /
    Accepted / Rejected views: the Streamlit process and the API process
    do not reliably share a filesystem (they don't at all once deployed as
    separate Cloud Run services), so a shared-disk read is fundamentally
    the wrong approach here, not just a path bug."""
    try:
        resp = requests.get(f"{API_BASE}/documents/{document_id}/image", timeout=15)
        return resp.content if resp.status_code == 200 else None
    except requests.RequestException:
        return None


def fetch_total_cost():
    try:
        resp = requests.get(f"{API_BASE}/metrics", timeout=10)
        if resp.status_code == 200:
            for line in resp.text.splitlines():
                parts = line.split()
                if len(parts) == 2 and parts[0] == "token_cost_usd_total":
                    return float(parts[1])
    except (requests.RequestException, ValueError):
        pass
    return None


def render_document_list(docs):
    """Shared card renderer for the Accepted / Rejected views."""
    if not docs:
        st.info("Nothing here yet.")
        return
    for doc in docs:
        with st.container(border=True):
            col1, col2 = st.columns([1, 3])
            with col1:
                img_bytes = fetch_document_image(doc["document_id"])
                if img_bytes:
                    st.image(img_bytes, width=220)
                else:
                    st.caption("No image available.")
            with col2:
                st.markdown(
                    f"**{doc['vendor'] or 'Unrecognized document'}** "
                    + status_badge(doc["status"]),
                    unsafe_allow_html=True,
                )
                st.caption(f"Document ID: {doc['document_id']}")
                st.write(f"Total: {doc['total']} {doc['currency'] or ''}".strip())
                st.caption(f"Processed: {doc.get('created_at') or 'unknown'}")
                if doc.get("extracted_json"):
                    with st.expander("Extracted data"):
                        try:
                            st.json(json.loads(doc["extracted_json"]))
                        except (TypeError, ValueError):
                            st.text(doc["extracted_json"])
                if doc.get("reviewed_json"):
                    with st.expander("Reviewer corrections"):
                        try:
                            st.json(json.loads(doc["reviewed_json"]))
                        except (TypeError, ValueError):
                            st.text(doc["reviewed_json"])


# --- Sidebar ------------------------------------------------------------
with st.sidebar:
    st.header("📄 LedgerLens")
    healthy = fetch_health()
    dot_color = "#2ecc71" if healthy else "#e74c3c"
    st.markdown(
        f'<span class="ll-health-dot" style="background:{dot_color};"></span>'
        f'API: {"healthy" if healthy else "unreachable"}',
        unsafe_allow_html=True,
    )
    st.caption(f"Connected to: {API_BASE}")
    st.divider()
    st.markdown("**Quick links**")
    st.markdown(f"- [API docs (Swagger)]({API_DOCS_URL}/docs)")
    st.markdown(f"- [Grafana dashboard]({GRAFANA_URL})")
    st.markdown(f"- [Prometheus targets]({PROMETHEUS_URL}/targets)")

# --- Header ---------------------------------------------------------------
st.title("📄 LedgerLens — Document Intelligence")
st.markdown(
    '<div class="ll-subtitle">Drop in a receipt or invoice — schema-validated '
    "extraction, per-field confidence, and a human review queue for anything "
    "the model isn't sure about.</div>",
    unsafe_allow_html=True,
)

# --- Navigation -------------------------------------------------------------
# Using st.radio for navigation, not st.tabs() or manually-styled buttons.
#
# st.tabs() has a long-standing, still-open Streamlit bug: calling
# st.rerun() from inside a tab resets the UI back to the FIRST tab, because
# the native tabs widget has no way to tell the frontend which tab should
# stay active after a rerun (see streamlit/streamlit#9249, #11160, #12554).
#
# A first attempt at working around this used two st.button()s with a
# type="primary"/"secondary" parameter that changed depending on which tab
# was active. That was itself buggy: Streamlit derives a button's identity
# partly from the parameters passed to it, so a parameter that changes
# value across reruns (like that dynamic `type`) can destabilize the
# widget's click-state tracking -- which is what caused clicks to not
# register reliably. It also used Streamlit's default "primary" button
# color, which is red, not something set intentionally.
#
# st.radio has neither problem: its selected value is a plain widget value
# tied to a stable `key`, which Streamlit reliably preserves across every
# rerun (including the st.rerun() calls after Approve/Reject/Refresh) with
# no manual rerun call needed for navigation at all, and no red styling.
nav_choice = st.radio(
    "Navigate",
    options=["📤 Upload", "🕵️ Review Queue", "✅ Accepted", "❌ Rejected"],
    horizontal=True,
    label_visibility="collapsed",
    key="active_tab_radio",
)
if nav_choice.startswith("📤"):
    active_tab = "Upload"
elif nav_choice.startswith("🕵️"):
    active_tab = "Review Queue"
elif nav_choice.startswith("✅"):
    active_tab = "Accepted"
else:
    active_tab = "Rejected"

st.divider()

# --- KPI row (every tab) ----------------------------------------------------
all_docs = fetch_documents(limit=1000)
status_counts: dict[str, int] = {}
for _d in all_docs:
    status_counts[_d["status"]] = status_counts.get(_d["status"], 0) + 1
total_cost = fetch_total_cost()

kpi_cols = st.columns(6)
kpi_cols[0].metric("Total documents", len(all_docs))
kpi_cols[1].metric("Pending review", status_counts.get("pending_review", 0))
kpi_cols[2].metric("Auto-approved", status_counts.get("auto_approved", 0))
kpi_cols[3].metric("Approved", status_counts.get("approved", 0))
kpi_cols[4].metric("Rejected", status_counts.get("rejected", 0))
kpi_cols[5].metric(
    "Extraction cost", f"${total_cost:.4f}" if total_cost is not None else "n/a"
)

st.divider()

# The file_uploader widget is keyed off this counter. Bumping the counter
# forces Streamlit to instantiate a brand new widget instance (fresh
# internal state) rather than reusing the previous one -- this is what
# guarantees a clean second/third/... upload instead of the widget
# getting stuck holding onto stale state from a prior file.
if "uploader_generation" not in st.session_state:
    st.session_state.uploader_generation = 0

# The last extraction result is stored here so it stays on screen across
# reruns. It only clears when "Clear / upload a different file" is
# explicitly clicked, or a new extraction succeeds and replaces it.
if "last_extraction" not in st.session_state:
    st.session_state.last_extraction = None

# Preview image bytes, stored separately from the live file_uploader value.
# This -- not `uploaded` -- is the single source of truth for the preview:
# Streamlit deletes a widget's stored value whenever that widget isn't
# instantiated during a given rerun, and since Upload/Review Queue/Accepted/
# Rejected are mutually-exclusive branches below, the file_uploader simply
# doesn't exist on any rerun where the user isn't on the Upload tab. Relying
# on the live `uploaded` value as the primary preview source meant the
# preview could vanish just from navigating to another tab and back, on top
# of being explicitly cleared by error branches (both bugs are fixed here:
# see the ingest response handling below, which no longer clears this on
# error, and the fact that this is now populated as soon as a file is
# chosen, independent of what the API responds).
if "last_extraction_image" not in st.session_state:
    st.session_state.last_extraction_image = None

# Persisted error/blocked message, shown until Clear is clicked -- same
# rationale as last_extraction_image: previously the error text only lived
# inside the button-click branch and disappeared on the next unrelated
# rerun (e.g. switching tabs), which read as "the result silently vanished".
if "last_error" not in st.session_state:
    st.session_state.last_error = None

if active_tab == "Upload":
    st.subheader("Upload a receipt or invoice")
    uploader_key = f"file_uploader_{st.session_state.uploader_generation}"
    uploaded = st.file_uploader("Image file (JPG/PNG)", type=["jpg", "jpeg", "png"], key=uploader_key)

    if uploaded is not None:
        st.session_state.last_extraction_image = uploaded.getvalue()
    preview_bytes = st.session_state.last_extraction_image

    if preview_bytes is not None:
        st.image(preview_bytes, caption="Preview", width=350)

    col_extract, col_reset = st.columns([1, 1])
    with col_extract:
        extract_clicked = st.button("Extract", disabled=uploaded is None)
    with col_reset:
        if st.button("Clear / upload a different file"):
            st.session_state.uploader_generation += 1
            st.session_state.last_extraction = None
            st.session_state.last_extraction_image = None
            st.session_state.last_error = None
            st.rerun()

    if uploaded is not None and extract_clicked:
        with st.spinner("Screening and extracting..."):
            files = {"file": (uploaded.name, uploaded.getvalue(), uploaded.type)}
            try:
                resp = requests.post(f"{API_BASE}/ingest", files=files, timeout=60)
            except requests.RequestException as exc:
                st.session_state.last_error = f"Could not reach API at {API_BASE}: {exc}"
                st.session_state.last_extraction = None
                resp = None

        if resp is not None:
            if resp.status_code == 422:
                detail = resp.json().get("detail", {})
                st.session_state.last_error = f"🚫 Blocked by moderation: {detail.get('blocked_reason')}"
                st.session_state.last_extraction = None
                # last_extraction_image is intentionally left untouched here.
                # A blocked/error result must not clear the preview -- it
                # should stay on screen until the user explicitly clicks
                # "Clear / upload a different file".
            elif resp.status_code == 200:
                st.session_state.last_extraction = resp.json()
                st.session_state.last_error = None
            else:
                st.session_state.last_error = f"Unexpected error: {resp.status_code} — {resp.text}"
                st.session_state.last_extraction = None

    if st.session_state.last_error:
        st.error(st.session_state.last_error)

    data = st.session_state.last_extraction
    if data is not None:
        st.divider()
        status = data["status"]
        st.markdown(
            f"**Document {data['document_id']}** " + status_badge(status),
            unsafe_allow_html=True,
        )
        if status == "pending_review":
            st.caption("Sent to the Review Queue tab for a human decision.")

        extracted = data["extracted"]
        st.json(extracted)

        if extracted and extracted.get("vendor") == "Unrecognized document":
            st.info(
                "This image didn't look like a receipt or invoice, so it wasn't "
                "auto-processed — it's been sent to the Review Queue tab so you "
                "can approve or reject it manually."
            )

        if data["flagged_fields"]:
            st.warning("Flagged low-confidence fields:")
            st.table(pd.DataFrame(data["flagged_fields"]))

        st.caption(f"Extraction cost: ${data['cost_usd']:.4f}")
        st.caption("This report stays here until you click \"Clear / upload a different file\" above.")

elif active_tab == "Review Queue":
    st.subheader("Documents pending review")
    if st.button("Refresh queue"):
        st.rerun()

    queue = fetch_review_queue()

    if not queue:
        st.info("No documents currently pending review. 🎉")

    for doc in queue:
        with st.container(border=True):
            title_vendor = doc["vendor"] or "Unrecognized document"
            header_col, badge_col = st.columns([4, 1])
            header_col.markdown(f"#### {title_vendor}")
            with badge_col:
                st.markdown(status_badge("pending_review"), unsafe_allow_html=True)
            st.caption(f"Document ID: {doc['document_id']}")

            col1, col2 = st.columns([1, 2])

            with col1:
                img_bytes = fetch_document_image(doc["document_id"])
                if img_bytes:
                    st.image(img_bytes, caption="Watermarked source", width=300)
                else:
                    st.warning("Source image not available from the API.")

                st.caption(f"**Uploaded:** {doc.get('created_at') or 'unknown'}")
                st.caption(f"**Moderation verdict:** {doc.get('moderation_verdict') or 'n/a'}")
                st.caption(f"**Filename:** {doc.get('filename') or 'n/a'}")

            with col2:
                extracted = {}
                if doc.get("extracted_json"):
                    try:
                        extracted = json.loads(doc["extracted_json"])
                    except (TypeError, ValueError):
                        extracted = {}

                m1, m2, m3 = st.columns(3)
                m1.metric("Total", f"{doc['total']} {doc['currency'] or ''}".strip())
                m2.metric("Invoice #", extracted.get("invoice_number") or "n/a")
                m3.metric("Date", extracted.get("date") or "n/a")

                st.write(f"**Overall confidence:** {extracted.get('overall_confidence', 'n/a')}")

                line_items = extracted.get("line_items") or []
                if line_items:
                    st.write("**Line items:**")
                    st.dataframe(pd.DataFrame(line_items), use_container_width=True)
                else:
                    st.caption("No line items extracted.")

                if st.checkbox("Show full extracted JSON", key=f"show_json_{doc['document_id']}"):
                    st.json(extracted)

                st.write("**Flagged low-confidence fields:**")
                flagged_df = pd.DataFrame(doc["flagged_fields"])
                if not flagged_df.empty:
                    flagged_df["corrected_value"] = ""
                    edited = st.data_editor(
                        flagged_df,
                        key=f"editor_{doc['document_id']}",
                        num_rows="fixed",
                        use_container_width=True,
                    )
                else:
                    st.caption("None flagged.")
                    edited = pd.DataFrame(columns=["field_path", "confidence", "corrected_value"])

                reviewer_name = st.text_input("Reviewer name", key=f"reviewer_{doc['document_id']}")

                col_approve, col_reject = st.columns([1, 1])

                with col_approve:
                    if st.button("✅ Approve", key=f"approve_{doc['document_id']}"):
                        field_updates = [
                            {"field_path": row["field_path"], "corrected_value": row["corrected_value"]}
                            for _, row in edited.iterrows()
                            if row.get("corrected_value")
                        ]
                        payload = {
                            "document_id": doc["document_id"],
                            "field_updates": field_updates,
                            "reviewer": reviewer_name or None,
                        }
                        try:
                            approve_resp = requests.post(f"{API_BASE}/approve", json=payload, timeout=30)
                        except requests.RequestException as exc:
                            st.error(f"Could not reach API to approve: {exc}")
                            approve_resp = None

                        if approve_resp is None:
                            pass
                        elif approve_resp.status_code == 200:
                            st.success("Approved! Moved to the Accepted tab.")
                            st.rerun()
                        else:
                            st.error(f"Approval failed: {approve_resp.text}")

                with col_reject:
                    if st.button("❌ Reject", key=f"reject_{doc['document_id']}"):
                        payload = {
                            "document_id": doc["document_id"],
                            "reviewer": reviewer_name or None,
                        }
                        try:
                            reject_resp = requests.post(f"{API_BASE}/reject", json=payload, timeout=30)
                        except requests.RequestException as exc:
                            st.error(f"Could not reach API to reject: {exc}")
                            reject_resp = None

                        if reject_resp is None:
                            pass
                        elif reject_resp.status_code == 200:
                            st.success("Rejected — moved to the Rejected tab.")
                            st.rerun()
                        else:
                            st.error(f"Rejection failed: {reject_resp.text}")

elif active_tab == "Accepted":
    st.subheader("Accepted documents")
    st.caption("Approved from the Review Queue, or auto-approved on ingest.")
    render_document_list(fetch_documents(status="approved") + fetch_documents(status="auto_approved"))

else:  # Rejected
    st.subheader("Rejected documents")
    st.caption("Rejected from the Review Queue.")
    render_document_list(fetch_documents(status="rejected"))
