"""
The gateway's login call to Team C.

The web login is phone-only. Team C's plain /auth/login now requires a
password, so the gateway must call the phone-only endpoint; calling /auth/login
made every login fail with 422, shown to patients as "enter a valid 10-digit
phone number" even when the number was fine.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
import requests

from services import auth_client
from services.auth_client import InvalidPhoneNumber, PhoneNotRegistered


class FakeResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = payload or {}

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.exceptions.HTTPError(str(self.status_code))


def test_login_calls_the_phone_only_endpoint_with_just_the_number(monkeypatch):
    seen = {}

    def fake_post(url, json=None, timeout=None):
        seen["url"], seen["json"] = url, json
        return FakeResponse(200, {"auth_id": "a1", "phone_no": "8722485312", "is_new": True})

    monkeypatch.setattr(requests, "post", fake_post)
    account = auth_client.login("8722485312")

    assert seen["url"].endswith("/api/v1/auth/phone-login")
    assert seen["json"] == {"phone_no": "8722485312"}
    assert account == {"auth_id": "a1", "phone_no": "8722485312", "is_new": True}


def test_a_422_still_means_malformed_number(monkeypatch):
    monkeypatch.setattr(requests, "post", lambda *a, **k: FakeResponse(422))
    with pytest.raises(InvalidPhoneNumber):
        auth_client.login("abc")


def test_a_401_or_403_means_the_number_cannot_be_used(monkeypatch):
    monkeypatch.setattr(requests, "post", lambda *a, **k: FakeResponse(401))
    with pytest.raises(PhoneNotRegistered):
        auth_client.login("8722485312")


def test_team_c_being_down_is_not_reported_as_a_bad_number(monkeypatch):
    def down(*args, **kwargs):
        raise requests.exceptions.ConnectionError("down")

    monkeypatch.setattr(requests, "post", down)
    with pytest.raises(requests.exceptions.RequestException):
        auth_client.login("8722485312")
