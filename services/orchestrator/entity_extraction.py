"""
Zenvy NLU - Intent classification and entity extraction.

Supports:
- 11 hospital intents
- doctor / department
- appointment date
- appointment time
- UHID
- confirmation
- emergency detection
- emergency symptoms
- urgency
- language detection

This module extracts information only from the CURRENT user message.
"""

import json
import re
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
# 11 SUPPORTED INTENTS
# =========================================================

SUPPORTED_INTENTS = [
    "Book Appointment",
    "Check Appointment",
    "Cancel/Reschedule",
    "Doctor Availability",
    "Department Information",
    "Emergency",
    "Bill Enquiry",
    "Visiting Hours",
    "General FAQ",
    "Human Request",
    "Unclear",
]


# =========================================================
# DATA STRUCTURE
# =========================================================

class ExtractedFields(TypedDict):
    intent: str
    language: str

    wants_to_book: bool
    doctor_name: Optional[str]
    appointment_date: Optional[str]
    appointment_time: Optional[str]
    uhid: Optional[str]
    confirms_booking: Optional[bool]

    is_emergency: bool
    emergency_symptoms: Optional[str]
    urgency: Optional[str]


# =========================================================
# LANGUAGE DETECTION
# =========================================================

def _detect_script_language(text: str) -> Optional[str]:
    """
    Detect Hindi/Kannada from their native scripts.

    Hindi -> Devanagari
    Kannada -> Kannada script

    Returns:
        "hi", "kn", or None
    """

    devanagari_count = len(
        re.findall(r"[\u0900-\u097F]", text)
    )

    kannada_count = len(
        re.findall(r"[\u0C80-\u0CFF]", text)
    )

    if devanagari_count > kannada_count and devanagari_count > 0:
        return "hi"

    if kannada_count > devanagari_count and kannada_count > 0:
        return "kn"

    return None


def _detect_language(text: str, sarvam_language: Optional[str]) -> str:
    """
    Detect language reliably.

    Native Hindi/Kannada scripts are detected locally first.
    For Latin-script English/code-mixed text, use Sarvam's result.
    """

    script_language = _detect_script_language(text)

    if script_language:
        return script_language

    if sarvam_language in ["en", "hi", "kn"]:
        return sarvam_language

    return "en"


# =========================================================
# EXTRACTION PROMPT
# =========================================================

EXTRACTION_SYSTEM_PROMPT = """
You are the NLU system for a hospital assistant.

Your job is to understand the patient's CURRENT message and return
ONLY ONE valid JSON object.

You must classify the message into EXACTLY ONE of these 11 intents:

1. Book Appointment
2. Check Appointment
3. Cancel/Reschedule
4. Doctor Availability
5. Department Information
6. Emergency
7. Bill Enquiry
8. Visiting Hours
9. General FAQ
10. Human Request
11. Unclear

Today's date is {today}.

=========================================================
INTENT DEFINITIONS
=========================================================

1. Book Appointment

Use when the patient wants to:
- book an appointment
- make an appointment
- see a doctor
- schedule a consultation
- get an appointment with a doctor or department

Examples:
"I want to book an appointment"
"I need to see a cardiologist tomorrow"
"Can I get an appointment with a dermatologist?"

---------------------------------------------------------

2. Check Appointment

Use when the patient wants to:
- check an existing appointment
- know appointment status
- know appointment date/time
- confirm details of an existing appointment

Examples:
"When is my appointment?"
"Can you check my appointment?"
"What time is my appointment?"

---------------------------------------------------------

3. Cancel/Reschedule

Use when the patient wants to:
- cancel an appointment
- change an appointment
- reschedule an appointment
- move an appointment to another date/time

Examples:
"I want to cancel my appointment"
"Can I reschedule my appointment?"
"Please change my appointment to tomorrow"

---------------------------------------------------------

4. Doctor Availability

Use when the patient asks whether:
- a particular doctor is available
- a doctor is working today
- a doctor is available at a particular time/date

Examples:
"Is Dr Kumar available today?"
"Is the cardiologist available tomorrow?"
"When is the doctor available?"

---------------------------------------------------------

5. Department Information

Use when the patient asks about:
- hospital departments
- which department handles a problem
- what services a department provides

Examples:
"Which department treats skin problems?"
"Do you have a cardiology department?"
"Which department should I visit for bone problems?"

---------------------------------------------------------

6. Emergency

Use when the patient describes a potentially urgent medical
situation requiring immediate medical attention.

Examples:
"I have severe chest pain"
"I cannot breathe"
"I am bleeding heavily"
"My child is unconscious"
"I broke my leg and it is bleeding badly"

Emergency must take priority over all other intents.

---------------------------------------------------------

7. Bill Enquiry

Use when the patient asks about:
- hospital bills
- billing
- payment
- charges
- cost
- invoice
- bill amount

Examples:
"How much is my hospital bill?"
"I want to check my bill"
"How can I pay my hospital bill?"
"Why is my bill so high?"

---------------------------------------------------------

8. Visiting Hours

Use when the patient asks about:
- visiting hours
- visitor timings
- when visitors are allowed
- ward visiting timings

Examples:
"What are the visiting hours?"
"When can I visit a patient?"
"What time can visitors come?"

---------------------------------------------------------

9. General FAQ

Use for general hospital questions that do not fit
the other specific intents.

Examples:
"Where is the hospital located?"
"Do you have parking?"
"How do I register?"
"Do you provide ambulance services?"

---------------------------------------------------------

10. Human Request

Use when the patient explicitly wants:
- a human
- a staff member
- reception
- nurse
- doctor
- customer support person

Examples:
"I want to talk to a human"
"Connect me to reception"
"Can I speak to someone?"
"I need to talk to a nurse"

---------------------------------------------------------

11. Unclear

Use when the message is:
- too vague
- incomplete
- meaningless
- impossible to classify confidently

Examples:
"Help"
"Okay"
"Something"
"asdfgh"

=========================================================
LANGUAGE
=========================================================

Detect the main language of the CURRENT message.

Return exactly one of:
- "en" for English
- "hi" for Hindi
- "kn" for Kannada

For mixed/code-mixed messages, return the dominant language.

IMPORTANT:
Do not guess the language from previous messages.
Only consider the CURRENT message.

=========================================================
BOOKING INFORMATION
=========================================================

Set wants_to_book = true when the patient wants to make
a new appointment.

Extract doctor or department if mentioned.

Normalize common descriptions:

"bone doctor" -> "Orthopaedics"
"bone specialist" -> "Orthopaedics"
"orthopedist" -> "Orthopaedics"
"orthopedic doctor" -> "Orthopaedics"

"skin doctor" -> "Dermatology"
"skin specialist" -> "Dermatology"
"dermatologist" -> "Dermatology"

"heart doctor" -> "Cardiology"
"heart specialist" -> "Cardiology"
"cardiologist" -> "Cardiology"

"children's doctor" -> "Paediatrics"
"child doctor" -> "Paediatrics"
"pediatrician" -> "Paediatrics"

"eye doctor" -> "Ophthalmology"
"eye specialist" -> "Ophthalmology"
"ophthalmologist" -> "Ophthalmology"

"ENT doctor" -> "ENT"

"dentist" -> "Dentistry"

If a specific doctor name is mentioned, return that name.

If no doctor or department is mentioned, return null.

=========================================================
APPOINTMENT DATE
=========================================================

Convert relative dates using today's date.

Examples:

"today" -> today's date
"tomorrow" -> tomorrow's date
"day after tomorrow" -> date two days from today
"next Monday" -> correct upcoming Monday

Return dates as YYYY-MM-DD.

If no date is mentioned, return null.

=========================================================
APPOINTMENT TIME
=========================================================

Convert times into 24-hour format.

Examples:

"3 PM" -> "15:00"
"3:30 PM" -> "15:30"
"8 AM" -> "08:00"
"8:30 PM" -> "20:30"
"10:30" -> "10:30"

If no time is mentioned, return null.

=========================================================
UHID
=========================================================

Extract the patient's UHID if it appears in the message.

UHID may appear with labels such as:
- UHID
- UHID number
- patient ID
- patient number

Return the identifier exactly as written, excluding the label.

If no UHID is mentioned, return null.

=========================================================
CONFIRMATION
=========================================================

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

Otherwise return null.

=========================================================
EMERGENCY DETECTION
=========================================================

Set is_emergency = true when the CURRENT message indicates
a potentially urgent medical situation.

Examples:

"severe chest pain"
"can't breathe"
"struggling to breathe"
"heavy bleeding"
"broke my leg and there is a lot of bleeding"
"broke my ankle badly"
"fainted"
"unconscious"
"need emergency medical help"
"severe pain"

Emergency must be classified as:

"Emergency"

even if the patient also asks for an appointment.

DO NOT diagnose the patient.

This is an application-level emergency flag, not a medical
diagnosis or definitive medical triage decision.

=========================================================
EMERGENCY SYMPTOMS
=========================================================

If is_emergency = true, summarize the emergency symptom.

Examples:

"I broke my ankle and it is bleeding"
-> "broken ankle with bleeding"

"I have severe chest pain"
-> "severe chest pain"

"I can't breathe"
-> "difficulty breathing"

"My child is unconscious"
-> "unconscious child"

If is_emergency = false:
return null.

=========================================================
URGENCY
=========================================================

Set urgency = "high" for potentially life-threatening,
severe, sudden, or urgent situations.

Examples:
- severe chest pain
- difficulty breathing
- unconsciousness
- heavy bleeding
- serious injury
- severe allergic reaction
- sudden loss of consciousness
- serious accident
- urgent emergency help

Set urgency = "normal" for ordinary non-emergency requests.

If there is not enough information:
return null.

=========================================================
IMPORTANT
=========================================================

Only extract information from the CURRENT message.

Do NOT carry previous values into this response.

Return ONLY valid JSON.

Required JSON format:

{{
  "intent": "Book Appointment",
  "language": "en",
  "wants_to_book": true,
  "doctor_name": "Cardiology",
  "appointment_date": "YYYY-MM-DD",
  "appointment_time": "15:00",
  "uhid": null,
  "confirms_booking": null,
  "is_emergency": false,
  "emergency_symptoms": null,
  "urgency": "normal"
}}
"""


# =========================================================
# NORMALIZATION
# =========================================================

def _normalize_department(
    doctor_name: Optional[str],
) -> Optional[str]:

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
# UHID EXTRACTION
# =========================================================

def _extract_uhid(text: str) -> Optional[str]:
    """
    Extract common UHID formats from the current message.
    """

    patterns = [
        r"\bUHID\s*(?:number|no\.?|#)?\s*(?:is|=|:|-)\s*([A-Za-z0-9][A-Za-z0-9\-/]*)",
        r"\bUHID\s*(?:number|no\.?|#)?\s+([A-Za-z0-9][A-Za-z0-9\-/]*)",
        r"\bpatient\s*(?:ID|number|no\.?)\s*(?:is|=|:|-)\s*([A-Za-z0-9][A-Za-z0-9\-/]*)",
        r"\bpatient\s*(?:ID|number|no\.?)\s+([A-Za-z0-9][A-Za-z0-9\-/]*)",
    ]

    for pattern in patterns:
        match = re.search(
            pattern,
            text,
            flags=re.IGNORECASE,
        )
        if match:
            return match.group(1)

    return None
# =========================================================
# EXTRACTION FUNCTION
# =========================================================

def extract_booking_fields(
    user_text: str,
) -> ExtractedFields:

    fallback: ExtractedFields = {
        "intent": "Unclear",
        "language": _detect_language(user_text, None),

        "wants_to_book": False,
        "doctor_name": None,
        "appointment_date": None,
        "appointment_time": None,
        "uhid": _extract_uhid(user_text),
        "confirms_booking": None,

        "is_emergency": False,
        "emergency_symptoms": None,
        "urgency": None,
    }

    if not user_text or not user_text.strip():
        return fallback

    system_prompt = EXTRACTION_SYSTEM_PROMPT.format(
        today=date.today().isoformat()
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

        # Remove markdown fences
        if raw_content.startswith("```"):
            raw_content = (
                raw_content
                .replace("```json", "")
                .replace("```", "")
                .strip()
            )

        # Parse JSON
        parsed = json.loads(raw_content)

        # -------------------------------------------------
        # Intent
        # -------------------------------------------------

        intent = parsed.get("intent")

        if intent not in SUPPORTED_INTENTS:
            intent = "Unclear"

        # -------------------------------------------------
        # Language
        # -------------------------------------------------

        sarvam_language = parsed.get("language")

        language = _detect_language(
            user_text,
            sarvam_language,
        )

        # -------------------------------------------------
        # Doctor / Department
        # -------------------------------------------------

        doctor_name = (
            parsed.get("doctor_name")
            or None
        )

        doctor_name = _normalize_department(
            doctor_name
        )

        # -------------------------------------------------
        # UHID
        # -------------------------------------------------

        uhid = _extract_uhid(user_text)
        if not uhid:
            parsed_uhid = parsed.get("uhid")
            if parsed_uhid and parsed_uhid.lower() not in {"is", "is:", "the"}:
                uhid = parsed_uhid

        # -------------------------------------------------
        # Emergency
        # -------------------------------------------------

        is_emergency = bool(
            parsed.get(
                "is_emergency",
                False,
            )
        )

        # Emergency always overrides other intents
        if is_emergency:
            intent = "Emergency"

        # -------------------------------------------------
        # Result
        # -------------------------------------------------

        result: ExtractedFields = {
            "intent": intent,
            "language": language,

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

            "uhid": uhid,

            "confirms_booking": (
                parsed.get(
                    "confirms_booking",
                    None,
                )
            ),

            "is_emergency": is_emergency,

            "emergency_symptoms": (
                parsed.get(
                    "emergency_symptoms"
                )
                or None
            ),

            "urgency": (
                parsed.get(
                    "urgency"
                )
                or None
            ),
        }

        print(
            "[NLU] INPUT:",
            user_text,
        )

        print(
            "[NLU] INTENT:",
            result["intent"],
        )

        print(
            "[NLU] LANGUAGE:",
            result["language"],
        )

        print(
            "[NLU] EXTRACTED:",
            result,
        )

        return result

    except Exception as e:

        print(
            "[NLU] Entity extraction failed:"
        )

        print(e)

        return fallback