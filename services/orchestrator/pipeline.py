"""
Zenvy full pipeline (Team B).

audio -> STT -> NLU -> LLM -> Translate -> response_text

Step 2: config, error type and the first three stages.
NLU/LLM are imported lazily so this module loads without an API key.
"""
import sys
import os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import requests

from services.language_codes import LANGUAGE_CODE_TO_SHORT

STT_URL = os.getenv("STT_URL", "http://localhost:8001")
STT_TIMEOUT = 30


class PipelineError(Exception):
    """Raised when a stage fails. Carries the stage name for clear errors."""

    def __init__(self, stage: str, message: str):
        self.stage = stage
        super().__init__(f"[{stage}] {message}")


def run_stt(audio_path: str) -> dict:
    """Send a WAV file to the STT service. Returns text, language_code, short_lang."""
    try:
        with open(audio_path, "rb") as f:
            response = requests.post(
                f"{STT_URL}/transcribe",
                files={"file": (Path(audio_path).name, f, "audio/wav")},
                timeout=STT_TIMEOUT,
            )
    except FileNotFoundError:
        raise PipelineError("stt", f"Audio file not found: {audio_path}")
    except requests.exceptions.ConnectionError:
        raise PipelineError("stt", f"STT service not reachable at {STT_URL}. Is it running?")
    except requests.exceptions.RequestException as e:
        raise PipelineError("stt", f"Request failed: {e}")

    if not response.ok:
        raise PipelineError("stt", f"HTTP {response.status_code}: {response.text[:300]}")

    data = response.json()
    text = (data.get("text") or "").strip()
    language_code = data.get("language_code")

    if not text:
        raise PipelineError("stt", "Empty transcript.")

    short_lang = LANGUAGE_CODE_TO_SHORT.get(language_code)
    if short_lang is None:
        raise PipelineError("stt", f"Unsupported or unknown language: {language_code!r}")

    return {"text": text, "language_code": language_code, "short_lang": short_lang}


def run_nlu(text: str) -> dict:
    """Call the NLU function directly (same logic as /understand)."""
    try:
        from services.orchestrator.entity_extraction import extract_booking_fields
        result = extract_booking_fields(text)
    except Exception as e:
        raise PipelineError("nlu", f"Extraction failed: {e}")

    return {
        "intent": result.get("intent"),
        "entities": {
            "doctor_name": result.get("doctor_name"),
            "appointment_date": result.get("appointment_date"),
            "appointment_time": result.get("appointment_time"),
            "uhid": result.get("uhid"),
        },
    }


def run_llm(text: str, short_lang: str) -> str:
    """Generate the reply in the patient's language."""
    try:
        from services.llm.client import generate_reply
        return generate_reply(text, short_lang)
    except ValueError as e:
        raise PipelineError("llm", str(e))
    except requests.exceptions.RequestException as e:
        raise PipelineError("llm", f"API call failed: {e}")
    except Exception as e:
        raise PipelineError("llm", f"Unexpected failure: {e}")
    
    # ---------------------------------------------------------------------------
# Step 3: Translate stage + full pipeline
# ---------------------------------------------------------------------------
import time

SHORT_TO_CODE = {short: code for code, short in LANGUAGE_CODE_TO_SHORT.items()}
TRANSLATE_MODEL = "sarvam-translate:v1"
TRANSLATE_TIMEOUT = 30


def detect_script_lang(text: str):
    """Guess 'kn', 'hi' or 'en' from the characters used. None if unclear."""
    kn = hi = latin = 0
    for ch in text:
        cp = ord(ch)
        if 0x0C80 <= cp <= 0x0CFF:
            kn += 1
        elif 0x0900 <= cp <= 0x097F:
            hi += 1
        elif ch.isalpha() and cp < 0x250:
            latin += 1
    counts = {"kn": kn, "hi": hi, "en": latin}
    best = max(counts, key=counts.get)
    return best if counts[best] > 0 else None


def translate_text(text: str, source_short: str, target_short: str) -> str:
    """Translate text between kn/hi/en using Sarvam Translate."""
    if source_short not in SHORT_TO_CODE or target_short not in SHORT_TO_CODE:
        raise PipelineError("translate", f"Unsupported pair: {source_short} -> {target_short}")
    if len(text) > 2000:
        raise PipelineError("translate", "Text exceeds the 2000 character limit.")

    try:
        from services.config import SARVAM_API_KEY, SARVAM_BASE_URL
        response = requests.post(
            f"{SARVAM_BASE_URL}/translate",
            headers={
                "api-subscription-key": SARVAM_API_KEY,
                "Content-Type": "application/json",
            },
            json={
                "input": text,
                "source_language_code": SHORT_TO_CODE[source_short],
                "target_language_code": SHORT_TO_CODE[target_short],
                "model": TRANSLATE_MODEL,
            },
            timeout=TRANSLATE_TIMEOUT,
        )
    except requests.exceptions.RequestException as e:
        raise PipelineError("translate", f"Request failed: {e}")
    except Exception as e:
        raise PipelineError("translate", f"Setup failed: {e}")

    if not response.ok:
        raise PipelineError("translate", f"HTTP {response.status_code}: {response.text[:300]}")

    translated = (response.json().get("translated_text") or "").strip()
    if not translated:
        raise PipelineError("translate", "Empty translation returned.")
    return translated


def run_pipeline(audio_path: str, target_lang: str = None) -> dict:
    """
    audio -> STT -> NLU -> LLM -> Translate -> response_text

    target_lang: 'kn' | 'hi' | 'en'. Default: the language the patient spoke.
    """
    if target_lang is not None and target_lang not in SHORT_TO_CODE:
        raise PipelineError("input", f"Unsupported target_lang: {target_lang!r}")

    timings = {}
    warnings = []
    t_start = time.perf_counter()

    t = time.perf_counter()
    stt = run_stt(audio_path)
    timings["stt"] = round((time.perf_counter() - t) * 1000)

    t = time.perf_counter()
    nlu = run_nlu(stt["text"])
    timings["nlu"] = round((time.perf_counter() - t) * 1000)

    t = time.perf_counter()
    llm_text = run_llm(stt["text"], stt["short_lang"])
    timings["llm"] = round((time.perf_counter() - t) * 1000)

    target = target_lang or stt["short_lang"]
    reply_lang = detect_script_lang(llm_text) or stt["short_lang"]
    response_text = llm_text
    translated = False

    t = time.perf_counter()
    if reply_lang != target:
        try:
            response_text = translate_text(llm_text, reply_lang, target)
            translated = True
        except PipelineError as e:
            warnings.append(str(e))
    timings["translate"] = round((time.perf_counter() - t) * 1000)
    timings["total"] = round((time.perf_counter() - t_start) * 1000)

    return {
        "transcript": stt["text"],
        "input_language": stt["short_lang"],
        "intent": nlu["intent"],
        "entities": nlu["entities"],
        "llm_text": llm_text,
        "target_language": target,
        "translated": translated,
        "response_text": response_text,
        "warnings": warnings,
        "timings_ms": timings,
    }