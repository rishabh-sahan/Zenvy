"""
Guards for the web dashboard (services/gateway/static/index.html).

The dashboard and the gateway talk through details that are easy to break
without noticing, and both have broken before:

* A spoken reply is audio, so the gateway returns the conversation session in
  the X-Session-Id header. If the page does not keep it, every turn starts a
  new conversation and booking asks the same questions forever.
* /channels/web/login reads the phone number as a FORM field. Sending JSON gets
  a 400, and the page then silently fell back to "demo mode".
"""
from pathlib import Path

PAGE = (Path(__file__).resolve().parent.parent / "services" / "gateway" / "static" / "index.html").read_text(
    encoding="utf-8"
)


def test_voice_replies_keep_the_session_from_the_header():
    assert 'headers.get("X-Session-Id")' in PAGE
    # ...and store it for the next turn.
    audio_branch = PAGE[PAGE.index('contentType.includes("audio")'):]
    assert 'localStorage.setItem' in audio_branch[: audio_branch.index("const audioBlob")]


def test_voice_replies_show_the_transcript_and_reply_text():
    assert '"X-Transcript"' in PAGE and '"X-Reply-Text"' in PAGE


def test_login_sends_the_phone_number_as_a_form_field():
    login = PAGE[PAGE.index("async function loginUser()"):PAGE.index("function showDashboard()")]
    assert 'append("phone_no", phone)' in login
    assert "JSON.stringify" not in login


def test_a_rejected_phone_number_is_reported_not_swallowed_by_demo_mode():
    login = PAGE[PAGE.index("async function loginUser()"):PAGE.index("function showDashboard()")]
    assert "error.rejected" in login


def test_a_fresh_login_starts_a_fresh_conversation():
    login = PAGE[PAGE.index("async function loginUser()"):PAGE.index("function showDashboard()")]
    assert 'removeItem("zenvy_session_id")' in login
