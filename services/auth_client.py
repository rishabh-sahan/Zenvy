"""
HTTP client for Team C's authentication endpoint.

Same boundary as services/conversation_client.py: Team A never queries the
authentication table directly -- Team C owns the schema and exposes it over
REST. This wraps POST /api/v1/auth/login.

The web login is phone-number-only and self-registering: an unknown number is
created on first use rather than rejected, so there is no separate signup step
and no password anywhere in the flow.
"""
import os

import requests

TEAM_C_BASE_URL = os.getenv("TEAM_C_BASE_URL", "http://127.0.0.1:8002")

_LOGIN_URL = f"{TEAM_C_BASE_URL}/api/v1/auth/login"


class PhoneNotRegistered(Exception):
    """
    Team C rejected the number.

    The current endpoint self-registers instead of rejecting, so this should
    not fire; it is kept so the gateway still reports a clean message if
    password or OTP verification is turned on later.
    """


class InvalidPhoneNumber(Exception):
    """The number was not in a shape Team C accepts (not 10 digits)."""


def login(phone_no: str) -> dict:
    """
    Sign a phone number in, registering it if Team C has not seen it before.

    Returns the account dict: {'auth_id', 'phone_no', 'is_new'}, where is_new
    is True when this call created the account.

    Raises InvalidPhoneNumber for a malformed number, PhoneNotRegistered if the
    service ever rejects one, and requests.exceptions.RequestException when
    Team C is unreachable -- callers should map that last one to a 502 rather
    than telling the patient their number is wrong.
    """
    response = requests.post(
        _LOGIN_URL,
        json={"phone_no": phone_no},
        timeout=10,
    )

    if response.status_code == 401:
        raise PhoneNotRegistered("This phone number is not registered.")

    if response.status_code == 422:
        raise InvalidPhoneNumber("Please enter a valid 10-digit phone number.")

    response.raise_for_status()
    return response.json()
