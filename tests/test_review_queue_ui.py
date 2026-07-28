"""Regression tests for the Streamlit reviewer UI's Review Queue tab.

Covers the reviewer-facing bugs in the Approve / Reject / Refresh queue
buttons:

  1. Approve/Reject left the document sitting in the queue, and printed no
     confirmation.
  2. Clicking a button bounced the reviewer back to the Upload tab, because
     the handlers ended in st.rerun(), which resets the nav radio.

The API is stubbed out at the `requests` layer so these run offline, with
no server, no database and no OpenAI credentials.
"""
import pytest
from streamlit.testing.v1 import AppTest

APP = "streamlit_app/app.py"
REVIEW_TAB = "🕵️ Review Queue"
UPLOAD_TAB = "📤 Upload"
NAV_KEY = "active_tab_radio"


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
        "flagged_fields": [
            {"field_path": "vendor", "confidence": 0.0},
            {"field_path": "total", "confidence": 0.1},
        ],
    }


class _Resp:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code
        self.text = payload if isinstance(payload, str) else str(payload)

    def json(self):
        return self._payload


class FakeAPI:
    """Minimal stand-in for the LedgerLens API.

    `stale` makes /review keep returning a document even after it has been
    approved/rejected -- the condition under which a resolved card could
    linger on screen.
    """

    def __init__(self, doc_ids, stale=False):
        self.pending = list(doc_ids)
        self.stale = stale
        self.posts = []

    def get(self, url, **kwargs):
        assert url.endswith("/review"), f"unexpected GET {url}"
        return _Resp([_doc(d) for d in self.pending])

    def post(self, url, json=None, **kwargs):
        action = url.rsplit("/", 1)[-1]
        self.posts.append((action, json))
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
    at.radio(key=NAV_KEY).set_value(REVIEW_TAB).run()
    return at


def _tab(at):
    return at.radio(key=NAV_KEY).value


def _rendered_doc_ids(at):
    """Each queue card is an expander labelled "<vendor> — <id>"."""
    return [e.label.split("—")[-1].strip() for e in at.expander]


def _click(at, key):
    [b for b in at.button if b.key == key][0].click().run()
    return at


# --- Navigation must survive every action ------------------------------------


def test_selecting_review_queue_shows_it_and_stays(api):
    """Clicking the Review Queue tab must show the queue and stay there."""
    api(["aaa"])
    at = AppTest.from_file(APP, default_timeout=30).run()
    assert _tab(at) == UPLOAD_TAB

    at.radio(key=NAV_KEY).set_value(REVIEW_TAB).run()
    assert _tab(at) == REVIEW_TAB
    assert _rendered_doc_ids(at) == ["aaa"], "Review Queue content did not render"

    at.run()  # an uninteracted rerun must not move it
    assert _tab(at) == REVIEW_TAB
    assert _rendered_doc_ids(at) == ["aaa"]


def test_refresh_queue_stays_on_review_tab(api):
    api(["aaa"])
    at = _open_review_tab(AppTest.from_file(APP, default_timeout=30).run())

    [b for b in at.button if b.label == "Refresh queue"][0].click().run()

    assert _tab(at) == REVIEW_TAB
    assert _rendered_doc_ids(at) == ["aaa"]
    assert not at.exception


def test_refresh_queue_refetches_the_queue(api):
    """Refresh must actually re-read the API, not just redraw."""
    fake = api(["aaa"])
    at = _open_review_tab(AppTest.from_file(APP, default_timeout=30).run())

    fake.pending = ["aaa", "bbb"]
    [b for b in at.button if b.label == "Refresh queue"][0].click().run()

    assert _rendered_doc_ids(at) == ["aaa", "bbb"]


def test_review_queue_never_calls_rerun():
    """st.rerun() in the Review Queue is what reset the nav radio. The
    Upload tab's own rerun (Clear / upload a different file) is fine and is
    deliberately not counted here."""
    source = open(APP).read()
    review_section = source[source.index("else:  # Review Queue"):]
    code_only = "\n".join(
        line for line in review_section.splitlines() if not line.strip().startswith("#")
    )
    assert "st.rerun()" not in code_only


@pytest.mark.parametrize("action", ["approve", "reject"])
def test_action_keeps_the_reviewer_on_the_review_tab(api, action):
    api(["aaa", "bbb"])
    at = _open_review_tab(AppTest.from_file(APP, default_timeout=30).run())

    _click(at, f"{action}_aaa")

    assert _tab(at) == REVIEW_TAB
    assert not at.exception


def test_user_can_still_navigate_between_tabs(api):
    api(["aaa"])
    at = AppTest.from_file(APP, default_timeout=30).run()

    at.radio(key=NAV_KEY).set_value(REVIEW_TAB).run()
    assert _tab(at) == REVIEW_TAB

    at.radio(key=NAV_KEY).set_value(UPLOAD_TAB).run()
    assert _tab(at) == UPLOAD_TAB
    assert _rendered_doc_ids(at) == [], "queue cards leaked onto the Upload tab"


# --- Approve/Reject must clear the card --------------------------------------


@pytest.mark.parametrize("stale", [False, True], ids=["api-updates", "api-stale"])
@pytest.mark.parametrize("action", ["approve", "reject"])
def test_action_removes_document_from_queue(api, action, stale):
    fake = api(["aaa", "bbb"], stale=stale)
    at = _open_review_tab(AppTest.from_file(APP, default_timeout=30).run())
    assert _rendered_doc_ids(at) == ["aaa", "bbb"]

    _click(at, f"{action}_aaa")

    assert not at.exception
    assert [a for a, _ in fake.posts] == [action]
    assert fake.posts[0][1]["document_id"] == "aaa"

    shown = _rendered_doc_ids(at)
    assert "aaa" not in shown, f"resolved doc still shown: {shown}"
    assert "bbb" in shown, "untouched doc was wrongly removed"

    assert any("aaa" in s.value for s in at.success), "no confirmation shown"


def test_resolved_ids_do_not_accumulate(api):
    """Once the API drops a document, the local hide-list must release it."""
    api(["aaa"])
    at = _open_review_tab(AppTest.from_file(APP, default_timeout=30).run())

    _click(at, "approve_aaa")

    assert at.session_state.resolved_docs == set()
    assert at.get("info"), "empty queue should show the 'nothing pending' notice"


def test_document_returning_to_queue_is_shown_again(api):
    """A hidden id must not permanently suppress that document."""
    fake = api(["aaa"], stale=True)
    at = _open_review_tab(AppTest.from_file(APP, default_timeout=30).run())

    _click(at, "approve_aaa")
    assert "aaa" not in _rendered_doc_ids(at)

    fake.pending = []
    at.run()
    fake.pending = ["aaa"]
    at.run()

    assert "aaa" in _rendered_doc_ids(at)


def test_failed_fetch_does_not_clear_the_hide_list(api, monkeypatch):
    """A transient API error must not resurrect a just-resolved document."""
    api(["aaa"], stale=True)
    at = _open_review_tab(AppTest.from_file(APP, default_timeout=30).run())
    _click(at, "approve_aaa")
    assert at.session_state.resolved_docs == {"aaa"}

    monkeypatch.setattr("requests.get", lambda *a, **k: _Resp("boom", status_code=500))
    at.run()

    assert at.session_state.resolved_docs == {"aaa"}
    assert at.error


def test_failed_action_keeps_the_document(api, monkeypatch):
    api(["aaa"])
    at = _open_review_tab(AppTest.from_file(APP, default_timeout=30).run())

    monkeypatch.setattr("requests.post", lambda *a, **k: _Resp("nope", status_code=500))
    _click(at, "approve_aaa")

    assert at.session_state.resolved_docs == set()
    assert "aaa" in _rendered_doc_ids(at)
    assert at.error
    assert _tab(at) == REVIEW_TAB


def test_unreachable_api_on_action_is_reported(api, monkeypatch):
    import requests as _requests

    api(["aaa"])
    at = _open_review_tab(AppTest.from_file(APP, default_timeout=30).run())

    def _boom(*a, **k):
        raise _requests.RequestException("connection refused")

    monkeypatch.setattr("requests.post", _boom)
    _click(at, "approve_aaa")

    assert at.error
    assert "aaa" in _rendered_doc_ids(at)


# --- Reviewer corrections must still reach the API ---------------------------


def test_reviewer_corrections_and_name_are_sent(api):
    """Approve reads the reviewer's edits out of the data_editor's session
    state. Row indices must map back to the right field_path."""
    fake = api(["aaa"])
    at = _open_review_tab(AppTest.from_file(APP, default_timeout=30).run())

    at.session_state["reviewer_aaa"] = "Priyatham"
    # Correct the *second* flagged row ("total"), to catch index mix-ups.
    at.session_state["editor_aaa"] = {
        "edited_rows": {1: {"corrected_value": "42.50"}},
        "added_rows": [],
        "deleted_rows": [],
    }
    _click(at, "approve_aaa")

    action, payload = fake.posts[0]
    assert action == "approve"
    assert payload["reviewer"] == "Priyatham"
    assert payload["field_updates"] == [{"field_path": "total", "corrected_value": "42.50"}]


def test_approve_with_no_corrections_sends_empty_updates(api):
    fake = api(["aaa"])
    at = _open_review_tab(AppTest.from_file(APP, default_timeout=30).run())

    _click(at, "approve_aaa")

    _, payload = fake.posts[0]
    assert payload["field_updates"] == []
    assert payload["reviewer"] is None


def test_reject_does_not_send_field_updates(api):
    fake = api(["aaa"])
    at = _open_review_tab(AppTest.from_file(APP, default_timeout=30).run())

    _click(at, "reject_aaa")

    action, payload = fake.posts[0]
    assert action == "reject"
    assert "field_updates" not in payload
