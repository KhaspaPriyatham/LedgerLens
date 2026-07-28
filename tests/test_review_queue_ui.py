"""Regression tests for the Streamlit reviewer UI's Review Queue tab.

Covers the two bugs fixed in the "durable nav" build:

  1. Approve/Reject left the document sitting in the queue.
  2. "Refresh queue" (and any other st.rerun()) bounced the reviewer back
     to the Upload tab.

The API is stubbed out at the `requests` layer so these run offline, with
no server, no database and no OpenAI credentials.
"""
import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

APP = "streamlit_app/app.py"
REVIEW_TAB = "🕵️ Review Queue"
UPLOAD_TAB = "📤 Upload"


def _doc(doc_id):
    return {
        "document_id": doc_id,
        "filename": f"{doc_id}.png",
        "vendor": None,  # renders as "Unrecognized document"
        "total": 0.0,
        "currency": "USD",
        "moderation_verdict": "allow",
        "created_at": "2026-07-28T00:00:00",
        "extracted_json": '{"vendor": "Unrecognized document", "line_items": [], "overall_confidence": 0.0}',
        "image_path": None,  # skips the image loader entirely
        "flagged_fields": [{"field_path": "vendor", "confidence": 0.0}],
    }


class _Resp:
    def __init__(self, payload, status_code=200, content=b""):
        self._payload = payload
        self.status_code = status_code
        self.text = payload if isinstance(payload, str) else str(payload)
        self.content = content

    def json(self):
        return self._payload


class FakeAPI:
    """Minimal stand-in for the LedgerLens API.

    `stale` makes /review keep returning a document even after it has been
    approved/rejected -- the exact condition under which the old UI left a
    resolved card on screen.
    """

    def __init__(self, doc_ids, stale=False):
        self.pending = list(doc_ids)
        self.stale = stale
        self.posts = []

    def get(self, url, **kwargs):
        if url.endswith("/review"):
            return _Resp([_doc(d) for d in self.pending])
        if url.endswith("/image"):
            return _Resp(None, status_code=404)
        if url.endswith("/metrics"):
            return _Resp("token_cost_usd_total 0.0")
        if url.endswith("/documents"):  # KPI row
            return _Resp([])
        if url.endswith("/health"):
            return _Resp({"status": "ok"})
        raise AssertionError(f"unexpected GET {url}")

    def post(self, url, json=None, **kwargs):
        self.posts.append((url.rsplit("/", 1)[-1], json["document_id"]))
        if not self.stale:
            self.pending.remove(json["document_id"])
        return _Resp({"document_id": json["document_id"], "status": "ok"})


@pytest.fixture
def api(monkeypatch):
    def _install(doc_ids, stale=False):
        fake = FakeAPI(doc_ids, stale=stale)
        monkeypatch.setattr("requests.get", fake.get)
        monkeypatch.setattr("requests.post", fake.post)
        return fake

    return _install


def _open_review_tab(at):
    at.radio(key="active_tab_radio").set_value(REVIEW_TAB).run()
    return at


def _rendered_doc_ids(at):
    """Each queue card renders `st.caption("Document ID: <id>")`."""
    return [
        c.value.split("Document ID:", 1)[1].strip()
        for c in at.caption
        if "Document ID:" in c.value
    ]


# --- Bug 2: navigation must survive every rerun ------------------------------


def test_refresh_queue_stays_on_review_tab(api):
    api(["aaa"])
    at = _open_review_tab(AppTest.from_file(APP, default_timeout=30).run())

    refresh = [b for b in at.button if b.label == "Refresh queue"][0]
    refresh.click().run()

    assert at.session_state.active_tab == "Review Queue"
    assert at.radio(key="active_tab_radio").value == REVIEW_TAB
    assert not at.exception


def test_nav_survives_a_widget_state_reset(api):
    """Even if the frontend reports the radio back at its first option, the
    durable `active_tab` key must win and re-assert the Review Queue."""
    api(["aaa"])
    at = _open_review_tab(AppTest.from_file(APP, default_timeout=30).run())

    # Simulate the frontend snapping the widget back to index 0.
    at.session_state["active_tab_radio"] = UPLOAD_TAB
    at.run()

    assert at.radio(key="active_tab_radio").value == REVIEW_TAB
    assert at.session_state.active_tab == "Review Queue"


def test_user_can_still_navigate_between_tabs(api):
    """The durability fix must not trap the reviewer on one tab."""
    api(["aaa"])
    at = AppTest.from_file(APP, default_timeout=30).run()
    assert at.session_state.active_tab == "Upload"

    at.radio(key="active_tab_radio").set_value(REVIEW_TAB).run()
    assert at.session_state.active_tab == "Review Queue"

    at.radio(key="active_tab_radio").set_value(UPLOAD_TAB).run()
    assert at.session_state.active_tab == "Upload"
    assert at.radio(key="active_tab_radio").value == UPLOAD_TAB


# --- Bug 1: approve/reject must clear the card -------------------------------


@pytest.mark.parametrize(
    "action, button_prefix",
    [("approve", "approve"), ("reject", "reject")],
)
@pytest.mark.parametrize("stale", [False, True], ids=["api-updates", "api-stale"])
def test_action_removes_document_from_queue(api, action, button_prefix, stale):
    fake = api(["aaa", "bbb"], stale=stale)
    at = _open_review_tab(AppTest.from_file(APP, default_timeout=30).run())
    assert _rendered_doc_ids(at) == ["aaa", "bbb"]

    [b for b in at.button if b.key == f"{button_prefix}_aaa"][0].click().run()

    assert not at.exception
    assert fake.posts == [(action, "aaa")]

    shown = _rendered_doc_ids(at)
    assert "aaa" not in shown, f"resolved doc still shown: {shown}"
    assert "bbb" in shown, "untouched doc was wrongly removed"

    # Still on the Review Queue, with a confirmation the reviewer can see.
    assert at.session_state.active_tab == "Review Queue"
    assert any("aaa" in s.value for s in at.success)


def test_resolved_ids_do_not_accumulate(api):
    """Once the API drops a document, the local hide-list must release it."""
    api(["aaa"])
    at = _open_review_tab(AppTest.from_file(APP, default_timeout=30).run())

    [b for b in at.button if b.key == "approve_aaa"][0].click().run()

    assert at.session_state.resolved_docs == set()
    assert at.get("info"), "empty queue should show the 'nothing pending' notice"


def test_document_returning_to_queue_is_shown_again(api):
    """A hidden id must not permanently suppress that document."""
    fake = api(["aaa"], stale=True)
    at = _open_review_tab(AppTest.from_file(APP, default_timeout=30).run())

    [b for b in at.button if b.key == "approve_aaa"][0].click().run()
    assert "aaa" not in _rendered_doc_ids(at)

    # API catches up and drops it, then it is re-queued later.
    fake.pending = []
    at.run()
    fake.pending = ["aaa"]
    at.run()

    assert "aaa" in _rendered_doc_ids(at)


def test_failed_fetch_does_not_clear_the_hide_list(api, monkeypatch):
    """A transient API error must not resurrect a just-resolved document."""
    fake = api(["aaa"], stale=True)
    at = _open_review_tab(AppTest.from_file(APP, default_timeout=30).run())
    [b for b in at.button if b.key == "approve_aaa"][0].click().run()
    assert at.session_state.resolved_docs == {"aaa"}

    monkeypatch.setattr("requests.get", lambda *a, **k: _Resp("boom", status_code=500))
    at.run()

    assert at.session_state.resolved_docs == {"aaa"}
    assert at.error


def test_failed_action_keeps_the_document(api, monkeypatch):
    api(["aaa"])
    at = _open_review_tab(AppTest.from_file(APP, default_timeout=30).run())

    monkeypatch.setattr("requests.post", lambda *a, **k: _Resp("nope", status_code=500))
    [b for b in at.button if b.key == "approve_aaa"][0].click().run()

    assert at.session_state.resolved_docs == set()
    assert "aaa" in _rendered_doc_ids(at)
    assert at.error
