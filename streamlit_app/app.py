import io
import json
import os

import pandas as pd
import requests
import streamlit as st
from PIL import Image

API_BASE = os.getenv("LEDGERLENS_API", "http://localhost:8000")

st.set_page_config(page_title="LedgerLens", layout="wide")
st.title("📄 LedgerLens — Document Intelligence")
st.caption("Build marker: 2026-07-28-v11 (approve/reject/refresh fix)")

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
# tied to a stable `key`, and no red styling.
#
# However, a widget key alone is NOT a durable place to keep the active
# tab. Streamlit only keeps widget-keyed state alive while that widget
# keeps being rendered, and the value the frontend reports back after an
# st.rerun() is what wins -- so a rerun fired from inside the Review Queue
# (Approve / Reject / Refresh queue) could snap the radio back to its first
# option and bounce the reviewer to the Upload tab.
#
# The fix is to keep the real answer in `active_tab`, an ordinary
# session_state key that is NOT owned by any widget and therefore survives
# every rerun untouched, and to treat the radio purely as an input device:
# clicks write into `active_tab` via on_change, and every run re-asserts the
# radio's value from `active_tab` before the widget is created. That
# assignment is a no-op on the run where the user actually clicked (the
# callback has already updated `active_tab` by then), so genuine navigation
# still works normally.
TAB_LABELS = {"Upload": "📤 Upload", "Review Queue": "🕵️ Review Queue"}
LABEL_TO_TAB = {label: tab for tab, label in TAB_LABELS.items()}

if "active_tab" not in st.session_state:
    st.session_state.active_tab = "Upload"


def _on_nav_change():
    st.session_state.active_tab = LABEL_TO_TAB[st.session_state.active_tab_radio]


# Re-assert the widget's value from the durable key. Must happen before the
# widget is instantiated -- assigning to a widget key afterwards is an error.
st.session_state.active_tab_radio = TAB_LABELS[st.session_state.active_tab]

st.radio(
    "Navigate",
    options=list(TAB_LABELS.values()),
    horizontal=True,
    label_visibility="collapsed",
    key="active_tab_radio",
    on_change=_on_nav_change,
)
active_tab = st.session_state.active_tab

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

# Preview image bytes, stored separately from the live file_uploader value,
# so the preview survives reruns without depending on widget re-serialization.
if "last_extraction_image" not in st.session_state:
    st.session_state.last_extraction_image = None

if active_tab == "Upload":
    st.subheader("Upload a receipt or invoice")
    uploader_key = f"file_uploader_{st.session_state.uploader_generation}"
    uploaded = st.file_uploader("Image file (JPG/PNG)", type=["jpg", "jpeg", "png"], key=uploader_key)

    preview_bytes = uploaded.getvalue() if uploaded is not None else st.session_state.last_extraction_image
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
            st.rerun()

    if uploaded is not None and extract_clicked:
        with st.spinner("Screening and extracting..."):
            files = {"file": (uploaded.name, uploaded.getvalue(), uploaded.type)}
            try:
                resp = requests.post(f"{API_BASE}/ingest", files=files, timeout=60)
            except requests.RequestException as exc:
                st.error(f"Could not reach API at {API_BASE}: {exc}")
                resp = None

        if resp is not None:
            if resp.status_code == 422:
                detail = resp.json().get("detail", {})
                st.error(f"🚫 Blocked by moderation: {detail.get('blocked_reason')}")
                st.session_state.last_extraction = None
                st.session_state.last_extraction_image = None
            elif resp.status_code == 200:
                st.session_state.last_extraction = resp.json()
                st.session_state.last_extraction_image = uploaded.getvalue()
            else:
                st.error(f"Unexpected error: {resp.status_code} — {resp.text}")
                st.session_state.last_extraction = None
                st.session_state.last_extraction_image = None

    data = st.session_state.last_extraction
    if data is not None:
        st.divider()
        status = data["status"]
        if status == "auto_approved":
            badge = "✅ auto-approved"
        elif status == "pending_review":
            badge = "🕵️ pending review — sent to Review Queue tab"
        else:
            badge = status
        st.success(f"Document {data['document_id']} — {badge}")

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

else:  # Review Queue
    st.subheader("Documents pending review")

    # Documents this session has already approved/rejected. The API is the
    # source of truth, but a card must disappear the instant its action
    # succeeds -- so resolved ids are hidden locally rather than depending
    # on the very next /review fetch already reflecting the write.
    if "resolved_docs" not in st.session_state:
        st.session_state.resolved_docs = set()

    # Anything reported by a completed action is shown once, after the
    # rerun. st.success() immediately followed by st.rerun() never paints,
    # because the rerun discards the page it was written to.
    if st.session_state.get("review_flash"):
        st.success(st.session_state.review_flash)
        st.session_state.review_flash = None

    if st.button("Refresh queue"):
        st.session_state.active_tab = "Review Queue"
        st.rerun()

    fetch_ok = True
    try:
        queue_resp = requests.get(f"{API_BASE}/review", timeout=30)
        if queue_resp.status_code == 200:
            queue = queue_resp.json()
        else:
            st.error(f"Review queue fetch failed: {queue_resp.status_code} — {queue_resp.text}")
            queue, fetch_ok = [], False
    except requests.RequestException as exc:
        st.error(f"Could not reach API at {API_BASE}: {exc}")
        queue, fetch_ok = [], False

    if fetch_ok:
        # Once the API stops returning a document, the local hide is
        # redundant -- drop it so the set can't grow without bound and
        # can't suppress a document that legitimately returns to the queue.
        returned_ids = {doc["document_id"] for doc in queue}
        st.session_state.resolved_docs &= returned_ids
        queue = [doc for doc in queue if doc["document_id"] not in st.session_state.resolved_docs]

    if not queue:
        st.info("No documents currently pending review. 🎉")

    for doc in queue:
        title_vendor = doc["vendor"] or "Unrecognized document"
        with st.expander(f"{title_vendor} — {doc['document_id']}"):
            col1, col2 = st.columns([1, 2])

            with col1:
                st.caption("🔧 image-loader-v2")
                image_path = doc.get("image_path")
                if not image_path:
                    st.warning("No source image path recorded for this document.")
                elif not os.path.exists(image_path):
                    st.warning(f"Source image file not found at: {image_path}")
                else:
                    img_bytes = None
                    try:
                        with open(image_path, "rb") as f:
                            img_bytes = f.read()
                        if not img_bytes:
                            raise ValueError("file is empty (0 bytes)")
                        # Force a full decode here (not just PIL.Image.open,
                        # which is lazy) so a truncated/corrupted file raises
                        # a clear Python exception now, rather than silently
                        # becoming the browser's generic broken-image icon
                        # with zero diagnostic information.
                        decoded = Image.open(io.BytesIO(img_bytes))
                        decoded.load()
                        st.caption(
                            f"Decoded OK: {decoded.format}, {decoded.size[0]}x{decoded.size[1]}, "
                            f"{len(img_bytes)} bytes on disk"
                        )
                        st.image(img_bytes, caption="Watermarked source", width=300)
                    except Exception as exc:
                        size = len(img_bytes) if img_bytes is not None else 0
                        st.error(f"Could not load source image: {type(exc).__name__}: {exc}")
                        st.caption(f"Path checked: {image_path} ({size} bytes read)")

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
                            st.session_state.resolved_docs.add(doc["document_id"])
                            st.session_state.review_flash = (
                                f"✅ Approved {doc['document_id']} — removed from the review queue."
                            )
                            st.session_state.active_tab = "Review Queue"
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
                            st.session_state.resolved_docs.add(doc["document_id"])
                            st.session_state.review_flash = (
                                f"❌ Rejected {doc['document_id']} — removed from the review queue."
                            )
                            st.session_state.active_tab = "Review Queue"
                            st.rerun()
                        else:
                            st.error(f"Rejection failed: {reject_resp.text}")
