"""
Structured slot extraction for appointment booking.

This is the "NLU" half of the roadmap's Day 32 requirement: the state
machine (state_machine.py) only advances slots that this module fills.
Reuses Sarvam Chat Completions (same API as services/llm/client.py)
but asks for JSON output instead of a spoken reply.
"""
import json
import sys
from datetime import date
from pathlib import Path
from typing import Optional, TypedDict

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import requests
from services.config import SARVAM_API_KEY, SARVAM_BASE_URL

CHAT_COMPLETIONS_URL = f"{SARVAM_BASE_URL}/v1/chat/completions"


class ExtractedFields(TypedDict):
    wants_to_book: bool
    doctor_name: Optional[str]
    appointment_date: Optional[str]   # ISO 8601 date, e.g. "2026-08-28"
    appointment_time: Optional[str]   # 24h time, e.g. "10:30"
    confirms_booking: Optional[bool]  # True/False/None (None = not applicable / unclear)


EXTRACTION_SYSTEM_PROMPT = (
    "You extract structured appointment-booking information from a patient's "
    "message at a hospital. Today's date is {today}.\n"
    "Respond with ONLY a single JSON object, no other text, no markdown fences, "
    "matching exactly this shape:\n"
    '{{"wants_to_book": true or false, '
    '"doctor_name": string or null, '
    '"appointment_date": "YYYY-MM-DD" or null, '
    '"appointment_time": "HH:MM" (24-hour) or null, '
    '"confirms_booking": true, false, or null}}\n'
    "Rules:\n"
    "- wants_to_book is true if the patient is trying to book, has an active "
    "booking in progress, or is providing booking details (doctor/date/time). "
    "It is false only for unrelated queries (e.g. asking hospital timings).\n"
    "- doctor_name: the doctor or department mentioned (e.g. 'dentist', "
    "'orthopedician'), or null if not mentioned in this message.\n"
    "- appointment_date: resolve relative dates like 'tomorrow' or 'next "
    "Monday' against today's date, output as YYYY-MM-DD, or null if not "
    "mentioned in this message.\n"
    "- appointment_time: output as 24-hour HH:MM, or null if not mentioned "
    "in this message.\n"
    "- confirms_booking: true if the patient is clearly confirming/agreeing "
    "(e.g. 'yes', 'that works', 'confirm it'), false if clearly declining/"
    "cancelling (e.g. 'no', 'cancel', 'never mind'), null if this message "
    "isn't a yes/no confirmation at all.\n"
    "Only extract what is explicitly in THIS message -- do not guess or "
    "carry over values from earlier turns."
)


def extract_booking_fields(user_text: str) -> ExtractedFields:
    """
    Run one extraction pass over a single user utterance. Returns null/False
    defaults if the API call fails or returns unparseable JSON, so a bad
    extraction degrades to "nothing new learned this turn" rather than
    crashing the conversation.
    """
    fallback: ExtractedFields = {
        "wants_to_book": False,
        "doctor_name": None,
        "appointment_date": None,
        "appointment_time": None,
        "confirms_booking": None,
    }

    system_prompt = EXTRACTION_SYSTEM_PROMPT.format(today=date.today().isoformat())

    try:
        response = requests.post(
            CHAT_COMPLETIONS_URL,
            headers={
                "api-subscription-key": SARVAM_API_KEY,
                "Content-Type": "application/json",
            },
            json={
                "model": "sarvam-105b-conversations",
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_text},
                ],
            },
            timeout=30,
        )
        response.raise_for_status()
        raw_content = response.json()["choices"][0]["message"]["content"].strip()

        # Strip accidental markdown fences, just in case the model adds them
        if raw_content.startswith("```"):
            raw_content = raw_content.strip("`")
            if raw_content.startswith("json"):
                raw_content = raw_content[4:].strip()

        parsed = json.loads(raw_content)
        return {
            "wants_to_book": bool(parsed.get("wants_to_book", False)),
            "doctor_name": parsed.get("doctor_name") or None,
            "appointment_date": parsed.get("appointment_date") or None,
            "appointment_time": parsed.get("appointment_time") or None,
            "confirms_booking": parsed.get("confirms_booking", None),
        }
    except Exception as e:
        print(f"[Orchestrator] Entity extraction failed, treating as no new info: {e}")
        return fallback