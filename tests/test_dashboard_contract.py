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


def test_there_is_a_logout_button_shown_only_while_logged_in():
    assert 'onclick="logoutUser()"' in PAGE
    # Hidden until showDashboard() reveals it.
    assert 'id="userChip" class="user-chip hidden"' in PAGE
    show = PAGE[PAGE.index("function showDashboard()"):PAGE.index("function showDashboard()") + 700]
    assert 'getElementById("userChip").classList.remove("hidden")' in show


def test_logout_forgets_the_patient_and_the_conversation():
    logout = PAGE[PAGE.index("function logoutUser()"):PAGE.index("function initMap()")]
    for key in ("zenvy_session_id", "zenvy_auth_id", "zenvy_phone"):
        assert f'localStorage.removeItem("{key}")' in logout
    assert "sessionId = null" in logout and "authId = null" in logout and "phoneNumber = null" in logout
    assert '"loginPage").classList.remove("hidden")' in logout
    assert '"dashboard").classList.add("hidden")' in logout


def test_logging_out_while_recording_does_not_upload_the_audio():
    logout = PAGE[PAGE.index("function logoutUser()"):PAGE.index("function initMap()")]
    assert "discardRecording = true" in logout
    stop_handler = PAGE[PAGE.index("mediaRecorder.onstop"):PAGE.index("await sendVoice(blob)")]
    assert "if (discardRecording)" in stop_handler


# ---------------------------------------------------------------------------
# doctor page
# ---------------------------------------------------------------------------

DOCTOR_PAGE = (Path(__file__).resolve().parent.parent / "services" / "gateway" / "static" / "doctor.html").read_text(
    encoding="utf-8"
)


def test_the_doctor_page_never_writes_patient_text_as_html():
    # Transcripts and notes come from speech and a language model: they must be
    # shown as text, never parsed as HTML.
    assert ".innerHTML" not in DOCTOR_PAGE
    assert "insertAdjacentHTML" not in DOCTOR_PAGE
    assert "document.write" not in DOCTOR_PAGE


def test_the_doctor_token_lives_only_as_long_as_the_tab():
    assert "sessionStorage.setItem(KEY, token)" in DOCTOR_PAGE
    assert "localStorage" not in DOCTOR_PAGE


def test_the_doctor_page_only_talks_to_the_gateway_doctor_api():
    assert 'fetch("/doctor/api/" + path' in DOCTOR_PAGE
    assert "http://" not in DOCTOR_PAGE.replace("http://www.w3.org", "")


def test_the_doctor_page_does_not_offer_to_overrule_a_patients_refusal():
    # The server refuses it too; the page must not even show the button.
    assert 'a.consent_recorded_by === "patient"' in DOCTOR_PAGE
    declined = DOCTOR_PAGE[DOCTOR_PAGE.index('if (a.consent_state === "declined") {'):]
    assert "byPatient ? null" in declined[:600]


def test_the_patient_dashboard_shows_recording_consent_without_building_html_from_data():
    card = PAGE[PAGE.index("function renderMyAppointment"):PAGE.index("async function setRecordingConsent")]
    assert "innerHTML" not in card and "textContent" in card
