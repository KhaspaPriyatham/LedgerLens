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
st.caption("Build marker: 2026-07-28-v13 (review queue images over HTTP)")


def fetch_document_image(document_id: str):
    """Fetch a document's watermarked image from the API. Returns
    (image_bytes, error_message); exactly one of the two is set.

    This goes over HTTP via GET /documents/{id}/image rather than opening
    the `image_path` recorded in the database. That path is only meaningful
    inside the API's own filesystem: the Streamlit process and the API
    process are separate containers under docker-compose, and separate
    services entirely once deployed, so reading the path directly only ever
    worked by the accident of a shared mount -- and silently showed
    "Source image file not found" the moment that mount wasn't there.
    """
    try:
        resp = requests.get(f"{API_BASE}/documents/{document_id}/image", timeout=15)
    except requests.RequestException as exc:
        return None, f"could not reach the API at {API_BASE} ({type(exc).__name__})"

    if resp.status_code == 200:
        return (resp.content, None) if resp.content else (None, "the API returned an empty image")
    if resp.status_code == 404:
        return None, "no image stored for this document"
    return None, f"the API returned HTTP {resp.status_code}"

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
# The radio is left exactly as it was -- plain `key`, no `index`, and
# nothing ever assigned to `active_tab_radio`. Two things that were tried
# here and did NOT work, so they don't get tried again:
#
#   - Assigning to the widget's key before each render, to force the tab
#     back. That races Streamlit's own widget machinery and lost: clicking
#     "Review Queue" landed back on Upload.
#   - Dropping the key and driving the radio with `index` instead. `index`
#     is part of a widget's identity, so changing it makes Streamlit treat
#     it as a brand new widget and throw the user's click away -- clicking
#     back to Upload from Review Queue silently did nothing.
#
# What actually keeps the tab put is further down: the Review Queue no
# longer calls st.rerun() at all. st.rerun() was the only thing resetting
# this radio, so the fix is to not need one. See the Review Queue section.
nav_choice = st.radio(
    "Navigate",
    options=["📤 Upload", "🕵️ Review Queue"],
    horizontal=True,
    label_visibility="collapsed",
    key="active_tab_radio",
)
active_tab = "Upload" if nav_choice.startswith("📤") else "Review Queue"

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

    # Documents this session has already approved/rejected. The callback
    # below runs before the /review fetch, so the API response should
    # already omit them -- this is a belt-and-braces hide in case a resolved
    # document still comes back, so a card can never linger after its action
    # succeeded.
    if "resolved_docs" not in st.session_state:
        st.session_state.resolved_docs = set()

    def _corrections_for(document_id, flagged_fields):
        """Read the reviewer's edits back out of the data_editor's state.

        A keyed st.data_editor stores a diff, not a DataFrame:
        {"edited_rows": {<row index>: {<column>: <new value>}}, ...}. Row
        indices line up with the flagged_fields list the table was built
        from, which is how each edit is mapped back to its field_path.
        """
        state = st.session_state.get(f"editor_{document_id}") or {}
        edited_rows = state.get("edited_rows", {}) if isinstance(state, dict) else {}
        updates = []
        for row_index, changes in edited_rows.items():
            value = changes.get("corrected_value")
            index = int(row_index)
            if value and 0 <= index < len(flagged_fields):
                updates.append(
                    {"field_path": flagged_fields[index]["field_path"], "corrected_value": value}
                )
        return updates

    def _resolve_document(action, document_id, flagged_fields):
        """Approve or reject, as an on_click callback. Runs before the
        script re-executes, so the queue fetch below already reflects it."""
        payload = {
            "document_id": document_id,
            "reviewer": st.session_state.get(f"reviewer_{document_id}") or None,
        }
        if action == "approve":
            payload["field_updates"] = _corrections_for(document_id, flagged_fields)

        try:
            resp = requests.post(f"{API_BASE}/{action}", json=payload, timeout=30)
        except requests.RequestException as exc:
            st.session_state.review_error = f"Could not reach API to {action}: {exc}"
            return

        if resp.status_code != 200:
            st.session_state.review_error = f"{action.capitalize()} failed: {resp.text}"
            return

        st.session_state.resolved_docs.add(document_id)
        verb = "✅ Approved" if action == "approve" else "❌ Rejected"
        st.session_state.review_flash = f"{verb} {document_id} — removed from the review queue."

    # Set by the Approve/Reject callbacks below, which run before this line
    # on the click's own rerun, so the outcome is reported straight away.
    # The old code called st.success() and then st.rerun() immediately after,
    # which never painted anything: the rerun throws away the page the
    # message was written to.
    if st.session_state.get("review_flash"):
        st.success(st.session_state.review_flash)
        st.session_state.review_flash = None
    if st.session_state.get("review_error"):
        st.error(st.session_state.review_error)
        st.session_state.review_error = None

    # No st.rerun() here on purpose. Clicking any button already reruns the
    # script from the top, which re-fetches the queue below -- that *is* the
    # refresh. The extra st.rerun() was redundant, and it was what knocked
    # the nav radio back to the Upload tab.
    st.button("Refresh queue")

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
                img_bytes, img_error = fetch_document_image(doc["document_id"])
                if img_bytes:
                    try:
                        # Force a full decode here (not just PIL.Image.open,
                        # which is lazy) so a truncated/corrupted image raises
                        # a clear Python exception now, rather than silently
                        # becoming the browser's generic broken-image icon
                        # with zero diagnostic information.
                        decoded = Image.open(io.BytesIO(img_bytes))
                        decoded.load()
                        st.image(img_bytes, caption="Watermarked source", width=300)
                    except Exception as exc:
                        st.error(f"Could not decode source image: {type(exc).__name__}: {exc}")
                        st.caption(f"{len(img_bytes)} bytes received from the API.")
                else:
                    st.warning(f"Source image not available: {img_error}")

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
                    st.data_editor(
                        flagged_df,
                        key=f"editor_{doc['document_id']}",
                        num_rows="fixed",
                        use_container_width=True,
                    )
                else:
                    st.caption("None flagged.")

                st.text_input("Reviewer name", key=f"reviewer_{doc['document_id']}")

                col_approve, col_reject = st.columns([1, 1])

                # Both actions run as on_click callbacks. Streamlit runs a
                # callback BEFORE re-executing the script, so by the time the
                # /review fetch above happens on this same click, the approve
                # or reject has already been committed and the document is
                # simply no longer in the response. That removes the need for
                # the st.rerun() these handlers used to end with -- which is
                # what was resetting the nav radio to the Upload tab.
                with col_approve:
                    st.button(
                        "✅ Approve",
                        key=f"approve_{doc['document_id']}",
                        on_click=_resolve_document,
                        args=("approve", doc["document_id"], doc["flagged_fields"]),
                    )

                with col_reject:
                    st.button(
                        "❌ Reject",
                        key=f"reject_{doc['document_id']}",
                        on_click=_resolve_document,
                        args=("reject", doc["document_id"], doc["flagged_fields"]),
                    )
