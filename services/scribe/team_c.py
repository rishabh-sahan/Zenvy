"""Client for Team C's consultation API.

Every call carries the doctor's own bearer token, so Team C decides what that
doctor may see; the gateway never holds a more powerful credential.
"""
import os

import requests

TEAM_C_BASE_URL = os.getenv("TEAM_C_BASE_URL", "http://127.0.0.1:8002")
TIMEOUT_SECONDS = 60


class TeamCError(Exception):
    """Team C answered with an error (or could not be reached)."""

    def __init__(self, status_code: int, detail: str):
        super().__init__(f"Team C {status_code}: {detail}")
        self.status_code = status_code
        self.detail = detail


class TeamC:
    def __init__(self, token: str, base_url: str | None = None, session=requests):
        self.base = f"{(base_url or TEAM_C_BASE_URL).rstrip('/')}/api/v1"
        self.headers = {"Authorization": f"Bearer {token}"}
        self.http = session

    def _call(self, method: str, path: str, **kwargs):
        try:
            response = self.http.request(
                method, f"{self.base}{path}", headers={**self.headers, **kwargs.pop("headers", {})},
                timeout=TIMEOUT_SECONDS, **kwargs,
            )
        except requests.exceptions.RequestException as exc:
            raise TeamCError(502, f"Team C is unreachable: {exc}") from exc
        if response.status_code >= 400:
            try:
                detail = response.json().get("detail", response.text)
            except ValueError:
                detail = response.text
            raise TeamCError(response.status_code, str(detail))
        if response.status_code == 204 or not response.content:
            return None
        return response.json()

    def get_consultation(self, consultation_id: str) -> dict:
        return self._call("GET", f"/consultations/{consultation_id}")

    def set_status(self, consultation_id: str, status: str, failure_reason: str | None = None) -> dict:
        return self._call(
            "PATCH", f"/consultations/{consultation_id}/status",
            json={"status": status, "failure_reason": failure_reason},
        )

    def put_transcript(self, consultation_id: str, turns: list[dict], language_code: str | None) -> dict:
        return self._call(
            "PUT", f"/consultations/{consultation_id}/transcript",
            json={"turns": turns, "language_code": language_code},
        )

    def add_note(self, consultation_id: str, note: dict, source: str) -> dict:
        return self._call("POST", f"/consultations/{consultation_id}/notes", json={**note, "source": source})

    def upload_audio(self, consultation_id: str, wav_bytes: bytes) -> dict:
        return self._call(
            "POST", f"/consultations/{consultation_id}/audio",
            data=wav_bytes, headers={"Content-Type": "audio/wav"},
        )

    # -- prescriptions, follow-ups and the doctor agent's inbox -----------------------------------

    def get_prescription(self, consultation_id: str) -> dict:
        return self._call("GET", f"/consultations/{consultation_id}/prescription")

    def put_prescription(self, consultation_id: str, items: list[dict], source: str) -> dict:
        return self._call("PUT", f"/consultations/{consultation_id}/prescription", json={"items": items, "source": source})

    def doctor_appointments(self) -> list[dict]:
        return self._call("GET", "/doctor/appointments")

    def patient_history(self, appointment_id: str) -> list[dict]:
        return self._call("GET", f"/appointments/{appointment_id}/patient-history")

    def messages(self, unread: bool = True) -> list[dict]:
        return self._call("GET", "/agent/messages", params={"unread": "true" if unread else "false"})

    def mark_messages_read(self, ids: list[str] | None = None) -> dict:
        return self._call("POST", "/agent/messages/read", json={"ids": ids})

    def get_follow_up(self, consultation_id: str) -> dict | None:
        return self._call("GET", f"/consultations/{consultation_id}/follow-up")

    def put_follow_up(self, consultation_id: str, day: str, at: str | None) -> dict:
        return self._call("PUT", f"/consultations/{consultation_id}/follow-up", json={"date": day, "time": at})

    def record_action(self, tool: str, summary: str | None, appointment_id: str | None = None, result: str = "ok") -> None:
        """Audit line for the doctor agent. Never raises."""
        try:
            self._call("POST", "/agents/actions", json={
                "agent": "doctor", "tool": tool, "summary": summary, "appointment_id": appointment_id, "result": result})
        except Exception as exc:  # noqa: BLE001 - auditing must not break the assistant
            print(f"[DoctorAgent] Could not record the action {tool!r}: {exc}")
