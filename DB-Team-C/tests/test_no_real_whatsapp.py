"""The safety net itself: a test can never reach the real WhatsApp API."""
import pytest

from app.core.config import settings
from app.services import whatsapp_service


def test_real_credentials_are_not_visible_to_tests():
    assert not settings.META_WHATSAPP_ACCESS_TOKEN
    assert not settings.META_WHATSAPP_PHONE_NUMBER_ID


def test_a_send_without_a_test_mock_fails_instead_of_going_out():
    with pytest.raises(RuntimeError, match="Missing Meta WhatsApp configuration"):
        whatsapp_service.send_template("9000000000", "zenvy_welcome", [("name", "x")])


def test_even_with_a_token_set_the_network_call_is_refused(monkeypatch):
    monkeypatch.setattr(settings, "META_WHATSAPP_ACCESS_TOKEN", "a-token")
    monkeypatch.setattr(settings, "META_WHATSAPP_PHONE_NUMBER_ID", "123")
    with pytest.raises(AssertionError, match="REAL WhatsApp"):
        whatsapp_service.send_template("9000000000", "zenvy_welcome", [("name", "x")])
