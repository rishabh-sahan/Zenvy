"""A DRAFT medicine list written from the consultation transcript.

This is only a draft for the doctor: it is marked "from transcript, verify", it is never shown to the
patient and it schedules nothing until the doctor edits and signs it. The model is told to use only what
was said, and then the code checks it: a medicine whose name does not appear anywhere in the transcript
or the note's plan is thrown away, so the model cannot invent one.
"""
import json
import re

from services.scribe import summarize

MAX_MEDICINES = 20
MAX_TRANSCRIPT_CHARS = 12000
FOODS = ("before", "after", "with", "any")

SYSTEM_PROMPT = (
    "You list the medicines a doctor prescribed in a consultation, from its transcript.\n\n"
    "Rules:\n"
    "1. Include ONLY medicines the DOCTOR told the patient to take. Never include medicines the patient "
    "says they already take, and never suggest any medicine yourself.\n"
    "2. Use ONLY what is said. If the dose, strength, frequency, number of days or food timing was not said, "
    "use null. Never guess or fill in typical values.\n"
    "3. Keep each medicine name exactly as spoken (write it in English letters).\n"
    "4. The Doctor/Patient labels were guessed automatically and may be wrong; use the meaning of the sentences.\n\n"
    "Reply with ONLY a JSON object, no other text: "
    '{"medicines": [ {"drug_name": string, "strength": string or null (e.g. "500 mg"), "form": string or null '
    '(tablet, capsule, syrup...), "dose_text": string or null (e.g. "1 tablet"), "frequency_text": string or null '
    '(as said, e.g. "twice a day" or "1-0-1"), "food": "before" | "after" | "with" | "any", '
    '"duration_days": integer or null, "as_needed": true or false, "instructions": string or null} ]}. '
    'If no medicine was prescribed reply {"medicines": []}.'
)


class MedicationError(Exception):
    def __init__(self, code: str, message: str = ""):
        super().__init__(message or code)
        self.code = code


def transcript_text(turns: list[dict]) -> str:
    lines = []
    for turn in turns:
        speaker = {"doctor": "Doctor", "patient": "Patient"}.get(turn.get("speaker"), "Speaker")
        lines.append(f"{speaker}: {turn.get('text', '').strip()}")
    return "\n".join(lines)[:MAX_TRANSCRIPT_CHARS]


def parse_medicines(reply: str) -> list[dict]:
    text = re.sub(r"<think>.*?</think>", "", reply or "", flags=re.S).strip()
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.M).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise MedicationError("medicines_failed", "The model did not return a medicine list.")
    try:
        data = json.loads(text[start:end + 1])
    except ValueError as exc:
        raise MedicationError("medicines_failed", "The model's medicine list was not valid JSON.") from exc
    medicines = data.get("medicines") if isinstance(data, dict) else None
    if not isinstance(medicines, list):
        raise MedicationError("medicines_failed", "The model's medicine list had the wrong shape.")
    return [m for m in medicines if isinstance(m, dict)]


def _words(value: str) -> list[str]:
    return [w for w in re.findall(r"[a-z]+", (value or "").lower()) if len(w) >= 4]


def normalise(raw: dict) -> dict | None:
    name = " ".join(str(raw.get("drug_name") or "").split())[:120]
    if not name:
        return None

    def text(key, limit):
        value = " ".join(str(raw.get(key) or "").split())[:limit]
        return value if value and value.lower() not in ("null", "none", "n/a") else None

    days = raw.get("duration_days")
    try:
        days = int(days) if days not in (None, "") else None
    except (TypeError, ValueError):
        days = None
    if days is not None and not 1 <= days <= 90:
        days = None
    food = str(raw.get("food") or "any").strip().lower()
    return {
        "drug_name": name, "strength": text("strength", 40), "form": text("form", 40),
        "dose_text": text("dose_text", 60), "frequency_text": text("frequency_text", 60),
        "food": food if food in FOODS else "any", "duration_days": days,
        "as_needed": bool(raw.get("as_needed")), "instructions": text("instructions", 300),
        "dose_times": [], "from_transcript": True,
    }


def keep_only_what_was_said(items: list[dict], evidence: str) -> tuple[list[dict], list[str]]:
    """A medicine stays only if (a word of) its name occurs in the transcript or the plan."""
    haystack = (evidence or "").lower()
    kept, dropped = [], []
    for item in items:
        words = _words(item["drug_name"])
        # a name with no Latin word of 4+ letters (a short brand like "Dolo") is checked whole
        found = any(w in haystack for w in words) if words else item["drug_name"].lower() in haystack
        (kept if found else dropped).append(item if found else item["drug_name"])
    return kept, dropped


def extract_medications(turns: list[dict], plan: str = "", chat=None) -> tuple[list[dict], list[str]]:
    """(draft items, names dropped because they were not in the transcript). Raises MedicationError."""
    if not turns:
        raise MedicationError("no_transcript", "There is no transcript to read the medicines from.")
    chat = chat or summarize.call_chat
    transcript = transcript_text(turns)
    try:
        reply = chat([
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Transcript:\n{transcript}\n\nDoctor's written plan (may be empty):\n{plan or ''}"},
        ])
    except summarize.SummaryError as exc:
        raise MedicationError("medicines_failed", str(exc)) from exc

    items = [n for n in (normalise(m) for m in parse_medicines(reply)) if n][:MAX_MEDICINES]
    return keep_only_what_was_said(items, f"{transcript}\n{plan or ''}")
