"""The English consultation note, written from the transcript with Sarvam chat.

The transcript may be Hindi, Kannada, English or a mix; the note is ALWAYS
English (a hard requirement in the roadmap, Day 96). The model is told to use
only what was said. A doctor reviews and signs every note before it counts.
"""
import json
import re
import time

import requests

CHAT_MODEL = "sarvam-105b-conversations"  # the same model the NLU uses
CHAT_TIMEOUT_SECONDS = 90
CHAT_ATTEMPTS = 3

# Longer transcripts are summarised in parts and then merged.
MAX_TRANSCRIPT_CHARS = 9000

# More than this share of non-Latin letters means the note is not in English.
MAX_NON_LATIN_SHARE = 0.10

FIELDS = ("chief_complaint", "discussion_points", "assessment", "plan")

SYSTEM_PROMPT = (
    "You are a clinical scribe. You read the transcript of a consultation between a doctor and a "
    "patient and write a short structured note for the doctor to review.\n\n"
    "Rules:\n"
    "1. Write the note in ENGLISH, even if the conversation is in Hindi, Kannada or a mix. "
    "Never write the note in another language.\n"
    "2. Use ONLY what is said in the transcript. Never invent symptoms, findings, diagnoses, "
    "medicines, doses, tests or dates. If something was not said, leave it empty. Do not add "
    "medical advice of your own.\n"
    "3. Keep medicine names, doses and numbers exactly as spoken.\n"
    "4. The Doctor/Patient labels were guessed automatically and may be wrong; use the meaning "
    "of the sentences to decide who said what.\n\n"
    "Reply with ONLY a JSON object, no other text, with exactly these keys:\n"
    '  "chief_complaint": string - the main problem the patient came with,\n'
    '  "discussion_points": array of short strings - the key things discussed (symptoms, history, '
    "findings),\n"
    '  "assessment": string - the doctor\'s assessment or diagnosis as stated,\n'
    '  "plan": string - treatment, medicines, tests, advice and follow-up as stated by the doctor.'
)

STRICT_SUFFIX = (
    "\n\nIMPORTANT: your previous answer was not in English. Every value in the JSON must be "
    "written in English letters and English words only."
)

MERGE_PROMPT = (
    "You are given several partial notes written from consecutive parts of ONE consultation. "
    "Combine them into a single note. Keep only what the parts say; do not add anything. Remove "
    "repetition. Reply with ONLY a JSON object with exactly the keys chief_complaint (string), "
    "discussion_points (array of short strings), assessment (string) and plan (string), all in English."
)


class SummaryError(Exception):
    """The note could not be written. `code` is a short machine-readable reason."""

    def __init__(self, code: str, message: str = ""):
        super().__init__(message or code)
        self.code = code


# ---------------------------------------------------------------------------
# calling the model
# ---------------------------------------------------------------------------

def call_chat(messages: list[dict]) -> str:
    """One Sarvam chat completion; returns the reply text. Retries transient errors."""
    from services.config import SARVAM_API_KEY, SARVAM_BASE_URL

    last_error = "unknown error"
    for attempt in range(1, CHAT_ATTEMPTS + 1):
        try:
            response = requests.post(
                f"{SARVAM_BASE_URL}/v1/chat/completions",
                headers={"api-subscription-key": SARVAM_API_KEY, "Content-Type": "application/json"},
                json={"model": CHAT_MODEL, "messages": messages},
                timeout=CHAT_TIMEOUT_SECONDS,
            )
        except requests.exceptions.RequestException as exc:
            last_error = f"request failed: {exc}"
        else:
            if response.status_code == 200:
                try:
                    return response.json()["choices"][0]["message"]["content"] or ""
                except (KeyError, IndexError, TypeError, ValueError) as exc:
                    raise SummaryError("summary_failed", "Unexpected reply from the language model.") from exc
            last_error = f"HTTP {response.status_code}"
            if response.status_code < 500 and response.status_code != 429:
                break
        if attempt < CHAT_ATTEMPTS:
            time.sleep(attempt * 2)
    raise SummaryError("summary_failed", f"The language model did not answer ({last_error}).")


# ---------------------------------------------------------------------------
# reading the model's answer
# ---------------------------------------------------------------------------

def parse_note(reply: str) -> dict:
    """Pull the JSON object out of a model reply and normalise the four fields."""
    text = re.sub(r"<think>.*?</think>", "", reply or "", flags=re.S).strip()
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.M).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise SummaryError("summary_failed", "The model did not return a note.")
    try:
        data = json.loads(text[start:end + 1])
    except ValueError as exc:
        raise SummaryError("summary_failed", "The model's note was not valid JSON.") from exc
    if not isinstance(data, dict):
        raise SummaryError("summary_failed", "The model's note had the wrong shape.")

    points = data.get("discussion_points") or []
    if isinstance(points, str):
        points = [line.strip("-• \t") for line in points.splitlines()]
    elif not isinstance(points, list):
        points = [str(points)]
    note = {
        "chief_complaint": str(data.get("chief_complaint") or "").strip(),
        "discussion_points": [str(p).strip() for p in points if str(p).strip()],
        "assessment": str(data.get("assessment") or "").strip(),
        "plan": str(data.get("plan") or "").strip(),
    }
    if not (note["chief_complaint"] or note["discussion_points"] or note["assessment"] or note["plan"]):
        raise SummaryError("summary_failed", "The model returned an empty note.")
    return note


def _note_text(note: dict) -> str:
    return " ".join([note["chief_complaint"], *note["discussion_points"], note["assessment"], note["plan"]])


def non_latin_share(note: dict) -> float:
    """Share of the note's letters that are not Latin (0 = all English letters)."""
    letters = [ch for ch in _note_text(note) if ch.isalpha()]
    if not letters:
        return 0.0
    return sum(1 for ch in letters if ord(ch) > 0x24F) / len(letters)


def is_english(note: dict) -> bool:
    return non_latin_share(note) <= MAX_NON_LATIN_SHARE


# ---------------------------------------------------------------------------
# writing the note
# ---------------------------------------------------------------------------

def format_transcript(turns: list[dict]) -> str:
    names = {"doctor": "Doctor", "patient": "Patient"}
    return "\n".join(f"{names.get(t.get('speaker'), 'Speaker')}: {t['text']}" for t in turns)


def _split_for_model(turns: list[dict]) -> list[str]:
    """Group turns into parts that each fit in one request."""
    parts, current, size = [], [], 0
    for turn in turns:
        line = format_transcript([turn])
        if current and size + len(line) > MAX_TRANSCRIPT_CHARS:
            parts.append("\n".join(current))
            current, size = [], 0
        current.append(line)
        size += len(line) + 1
    if current:
        parts.append("\n".join(current))
    return parts


def _write_note(transcript: str, chat, system: str = SYSTEM_PROMPT) -> dict:
    reply = chat([
        {"role": "system", "content": system},
        {"role": "user", "content": "Transcript:\n\n" + transcript},
    ])
    return parse_note(reply)


def _translate_note(note: dict, translate) -> dict:
    """Last resort: translate the note's fields to English one by one."""
    def one(text: str) -> str:
        return translate(text) if text else text

    return {
        "chief_complaint": one(note["chief_complaint"]),
        "discussion_points": [one(p) for p in note["discussion_points"]],
        "assessment": one(note["assessment"]),
        "plan": one(note["plan"]),
    }


def _default_translate(text: str) -> str:
    """Translate to English with Sarvam Translate (source language guessed from the script)."""
    from services.orchestrator.pipeline import detect_script_lang, translate_text

    source = detect_script_lang(text)
    if source in (None, "en"):
        return text
    return translate_text(text, source, "en")


def summarize(turns: list[dict], chat=call_chat, translate=_default_translate) -> dict:
    """The structured English note for a transcript.

    Returns {"chief_complaint", "discussion_points", "assessment", "plan"}.
    Raises SummaryError("empty_transcript") if there is nothing to summarise and
    SummaryError("summary_failed") if the model cannot produce a usable note.
    """
    if not [t for t in turns if (t.get("text") or "").strip()]:
        raise SummaryError("empty_transcript", "There is no speech to summarise.")

    parts = _split_for_model(turns)

    def write(transcript: str) -> dict:
        note = _write_note(transcript, chat)
        if not is_english(note):
            note = _write_note(transcript, chat, SYSTEM_PROMPT + STRICT_SUFFIX)
        return note

    notes = [write(part) for part in parts]

    if len(notes) == 1:
        note = notes[0]
    else:
        merged = chat([
            {"role": "system", "content": MERGE_PROMPT},
            {"role": "user", "content": json.dumps(notes, ensure_ascii=False)},
        ])
        try:
            note = parse_note(merged)
        except SummaryError:
            # Keep everything rather than lose a part.
            note = {
                "chief_complaint": notes[0]["chief_complaint"],
                "discussion_points": [p for n in notes for p in n["discussion_points"]],
                "assessment": " ".join(n["assessment"] for n in notes if n["assessment"]),
                "plan": " ".join(n["plan"] for n in notes if n["plan"]),
            }

    if not is_english(note):
        note = _translate_note(note, translate)
        if not is_english(note):
            raise SummaryError("summary_not_english", "The note could not be produced in English.")
    return note
