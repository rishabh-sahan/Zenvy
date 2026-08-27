"""
LLM response-generation client.
Wraps Sarvam's Chat Completions endpoint so the conversational "brain"
(normally Team B's Orchestrator/NLU scope, roadmap Days 26-35) can turn
a patient's transcribed text into a reply, in the same language the
patient used.

This is a deliberately small, standalone piece: no intent/entity
extraction, no state machine, no persistence. It answers a single
turn at a time. Session-aware, multi-turn booking logic is still the
real Orchestrator's job later.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import requests
from services.config import SARVAM_API_KEY, SARVAM_BASE_URL

assert SARVAM_API_KEY is not None

CHAT_COMPLETIONS_URL = f"{SARVAM_BASE_URL}/v1/chat/completions"

# Maps our internal short language code to the name used in the prompt,
# so the model is told explicitly which language to reply in rather than
# guessing from the input text alone.
LANGUAGE_NAMES = {
    "kn": "Kannada",
    "hi": "Hindi",
    "en": "English",
}

SYSTEM_PROMPT = (
    "You are a hospital receptionist AI assistant for the Zenvy Hospital Pilot. "
    "Answer patient questions concisely and only about hospital-appropriate topics: "
    "appointments, hospital timings, departments, general navigation, and basic "
    "administrative queries.\n"
    "For everyday MINOR PHYSICAL discomfort only (e.g. a mild headache, mild "
    "acidity, minor fatigue, mild muscle ache), you may offer well-known general "
    "self-care tips: rest, hydration, avoiding known triggers, eating light food. "
    "Never name a specific medicine, supplement, dosage, or brand, and never "
    "diagnose a condition. Always end such a reply by telling the patient to see "
    "a doctor if it is severe, unusual, or does not improve.\n"
    "If a patient discloses anything emotional or mental-health related -- "
    "sadness, stress, anxiety, depression, grief, relationship difficulties, or "
    "similar -- do NOT respond with physical self-care tips like hydration or "
    "rest, and do NOT treat it as minor. Respond briefly and warmly, acknowledge "
    "what they shared, and gently encourage them to speak with a doctor or "
    "counsellor at the hospital. Never try to counsel, diagnose, or resolve the "
    "emotional issue yourself.\n"
    "For anything beyond minor everyday physical discomfort, or anything you are "
    "not sure is minor, do not guess -- redirect the patient to speak with a "
    "doctor or nurse instead.\n"
    "Keep replies short (1-2 sentences), natural, and spoken aloud, since they "
    "will be converted to speech. Reply ONLY in {language}, regardless of what "
    "language this instruction is written in."
)


def generate_reply(user_text: str, short_lang: str) -> str:
    """
    Send the patient's transcribed text to Sarvam Chat Completions and
    return a hospital-appropriate reply in the same language.

    Raises requests.exceptions.RequestException on transport/API failure,
    and ValueError if the language code isn't one we support -- callers
    should catch these the same way the STT/TTS service clients do.
    """
    if short_lang not in LANGUAGE_NAMES:
        raise ValueError(f"Unsupported language: '{short_lang}'. Allowed: {sorted(LANGUAGE_NAMES)}")

    system_prompt = SYSTEM_PROMPT.format(language=LANGUAGE_NAMES[short_lang])

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
    if not response.ok:
        print(f"[LLM] Sarvam API error {response.status_code}: {response.text}")
    response.raise_for_status()

    result = response.json()
    reply_text = result["choices"][0]["message"]["content"].strip()
    return reply_text