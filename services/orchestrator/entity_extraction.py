"""
Structured slot extraction for appointment booking.

Extracts:
- booking intent
- doctor / department
- appointment date
- appointment time
- confirmation

The state machine stores previous values in Redis, so this module
only extracts information from the CURRENT user message.
"""

import json
import sys
from datetime import date
from pathlib import Path
from typing import Optional, TypedDict

import requests

sys.path.insert(
    0,
    str(Path(__file__).resolve().parent.parent.parent),
)

from services.config import (
    SARVAM_API_KEY,
    SARVAM_BASE_URL,
)


CHAT_COMPLETIONS_URL = (
    f"{SARVAM_BASE_URL}/v1/chat/completions"
)


# =========================================================
# DATA STRUCTURE
# =========================================================

class ExtractedFields(TypedDict):
    wants_to_book: bool
    doctor_name: Optional[str]
    appointment_date: Optional[str]
    appointment_time: Optional[str]
    confirms_booking: Optional[bool]


# =========================================================
# EXTRACTION PROMPT
# =========================================================

EXTRACTION_SYSTEM_PROMPT = """
You extract structured appointment-booking information from a
patient's message at a hospital.

Today's date is {today}.

Return ONLY one valid JSON object.

Required format:

{{
  "wants_to_book": true or false,
  "doctor_name": string or null,
  "appointment_date": "YYYY-MM-DD" or null,
  "appointment_time": "HH:MM" or null,
  "confirms_booking": true or false or null
}}

RULES:

1. BOOKING INTENT

Set wants_to_book = true when the patient:
- wants to book an appointment
- wants a booking
- wants to make an appointment
- says "I need an appointment"
- says "I want to see a doctor"
- provides appointment details such as doctor, department,
  date, or time as part of a booking conversation.

Examples:

"I want to book an appointment"
=> true

"I wanted a booking appointment"
=> true

"I need to see a doctor tomorrow"
=> true

"I wanted to book at 3 PM"
=> true


2. DOCTOR / DEPARTMENT

Extract the doctor or department mentioned.

IMPORTANT:
Normalize common natural-language descriptions.

Examples:

"bone doctor"
=> "Orthopaedics"

"bone specialist"
=> "Orthopaedics"

"orthopedist"
=> "Orthopaedics"

"orthopedic doctor"
=> "Orthopaedics"

"skin doctor"
=> "Dermatology"

"dermatologist"
=> "Dermatology"

"heart doctor"
=> "Cardiology"

"cardiologist"
=> "Cardiology"

"children's doctor"
=> "Paediatrics"

"pediatrician"
=> "Paediatrics"

"eye doctor"
=> "Ophthalmology"

"ENT doctor"
=> "ENT"

"dentist"
=> "Dentistry"

If a specific doctor name is mentioned, return that doctor name.

If no doctor or department is mentioned:
return null.


3. APPOINTMENT DATE

Convert relative dates using today's date.

Examples:

"today"
=> today's date

"tomorrow"
=> tomorrow's date

"day after tomorrow"
=> date two days from today

"next Monday"
=> correct upcoming Monday

Return YYYY-MM-DD.

If no date is mentioned:
return null.


4. APPOINTMENT TIME

Convert times into 24-hour format.

Examples:

"3 PM"
=> "15:00"

"3:30 PM"
=> "15:30"

"8 AM"
=> "08:00"

"8:30 PM"
=> "20:30"

"10:30"
=> "10:30"

If no time is mentioned:
return null.


5. CONFIRMATION

Set confirms_booking = true for:

"yes"
"yes please"
"confirm"
"confirm it"
"book it"
"that works"
"that's fine"
"go ahead"

Set confirms_booking = false for:

"no"
"cancel"
"cancel it"
"never mind"
"don't book it"

Otherwise:
null.


6. IMPORTANT

Only extract information from the CURRENT message.

Do NOT carry previous values into this response.

The appointment state machine will handle previous values.
"""


# =========================================================
# NORMALIZATION
# =========================================================

def _normalize_department(
    doctor_name: Optional[str],
) -> Optional[str]:
    """
    Normalize natural-language doctor descriptions
    into hospital departments.
    """

    if not doctor_name:
        return None

    value = doctor_name.strip()

    lower = value.lower()

    # Orthopaedics
    orthopaedics_terms = [
        "bone doctor",
        "bone specialist",
        "orthopedist",
        "orthopaedic",
        "orthopedic",
        "orthopedics",
        "orthopaedics",
        "ortho doctor",
        "ortho",
    ]

    for term in orthopaedics_terms:
        if term in lower:
            return "Orthopaedics"

    # Cardiology
    cardiology_terms = [
        "heart doctor",
        "heart specialist",
        "cardiologist",
        "cardiology",
    ]

    for term in cardiology_terms:
        if term in lower:
            return "Cardiology"

    # Dermatology
    dermatology_terms = [
        "skin doctor",
        "skin specialist",
        "dermatologist",
        "dermatology",
    ]

    for term in dermatology_terms:
        if term in lower:
            return "Dermatology"

    # Paediatrics
    paediatrics_terms = [
        "children doctor",
        "children's doctor",
        "child doctor",
        "pediatrician",
        "paediatrician",
        "pediatrics",
        "paediatrics",
    ]

    for term in paediatrics_terms:
        if term in lower:
            return "Paediatrics"

    # Ophthalmology
    eye_terms = [
        "eye doctor",
        "eye specialist",
        "ophthalmologist",
        "ophthalmology",
    ]

    for term in eye_terms:
        if term in lower:
            return "Ophthalmology"

    # ENT
    ent_terms = [
        "ent doctor",
        "ent specialist",
        "ent",
    ]

    for term in ent_terms:
        if term in lower:
            return "ENT"

    # Dentistry
    dental_terms = [
        "dentist",
        "dental doctor",
        "dentistry",
    ]

    for term in dental_terms:
        if term in lower:
            return "Dentistry"

    return value


# =========================================================
# EXTRACTION FUNCTION
# =========================================================

def extract_booking_fields(
    user_text: str,
) -> ExtractedFields:

    fallback: ExtractedFields = {
        "wants_to_book": False,
        "doctor_name": None,
        "appointment_date": None,
        "appointment_time": None,
        "confirms_booking": None,
    }

    system_prompt = (
        EXTRACTION_SYSTEM_PROMPT.format(
            today=date.today().isoformat()
        )
    )

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
                    {
                        "role": "system",
                        "content": system_prompt,
                    },
                    {
                        "role": "user",
                        "content": user_text,
                    },
                ],
            },

            timeout=30,
        )

        response.raise_for_status()

        raw_content = (
            response
            .json()
            ["choices"][0]
            ["message"]
            ["content"]
            .strip()
        )

        # -------------------------------------------------
        # Remove markdown fences
        # -------------------------------------------------

        if raw_content.startswith("```"):

            raw_content = (
                raw_content
                .replace("```json", "")
                .replace("```", "")
                .strip()
            )

        # -------------------------------------------------
        # Parse JSON
        # -------------------------------------------------

        parsed = json.loads(
            raw_content
        )

        doctor_name = (
            parsed.get("doctor_name")
            or None
        )

        # -------------------------------------------------
        # Normalize department
        # -------------------------------------------------

        doctor_name = (
            _normalize_department(
                doctor_name
            )
        )

        result: ExtractedFields = {

            "wants_to_book": bool(
                parsed.get(
                    "wants_to_book",
                    False,
                )
            ),

            "doctor_name": doctor_name,

            "appointment_date": (
                parsed.get(
                    "appointment_date"
                )
                or None
            ),

            "appointment_time": (
                parsed.get(
                    "appointment_time"
                )
                or None
            ),

            "confirms_booking": (
                parsed.get(
                    "confirms_booking",
                    None,
                )
            ),
        }

        print(
            "[Orchestrator] INPUT:",
            user_text,
        )

        print(
            "[Orchestrator] EXTRACTED:",
            result,
        )

        return result

    except Exception as e:

        print(
            "[Orchestrator] Entity extraction failed:"
        )

        print(e)

        return fallback