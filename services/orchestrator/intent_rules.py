"""Recognising "cancel" and "reschedule" by keywords (English, Hindi, Kannada).

The language model that classifies intent is unreliable on short phrases, so the
common ways of asking are read by rules first; the model's "Cancel/Reschedule"
intent is only a fallback. These rules are deliberately cautious: "don't cancel"
is not a cancellation, and a stray "change" is not about an appointment.
"""
import re

_CANCEL_EN = re.compile(r"\b(cancel\w*|call off|drop (?:my|the|this) appointment|don'?t want (?:my|the|this) appointment)\b")
_RESCHEDULE_EN = re.compile(
    r"\b(re-?schedul\w*|postpone\w*|prepone\w*|"
    r"(?:move|change|shift|swap)\b.{0,25}\b(?:appointment|booking|visit|slot)s?\b|"
    r"\b(?:appointment|booking|visit)s?\b.{0,25}\b(?:move|change|shift)\b)"
)
# "don't cancel", "do not reschedule", "no need to cancel"
_NEGATED = re.compile(r"\b(?:don'?t|do not|never|not|no need to|without|didn'?t)\s+(?:\w+\s+){0,2}(?:cancel|re-?schedul|postpone)")

_CANCEL_OTHER = ("रद्द", "कैंसल", "ರದ್ದು", "ರದ್ದುಗೊಳಿ")
_RESCHEDULE_OTHER = ("रीशेड्यूल", "री-शेड्यूल", "टाल", "बदल", "आगे बढ़ा", "ಬದಲಾಯಿಸ", "ಮುಂದೂಡ", "ಬದಲು")
# Hindi/Kannada "change" words are common, so they only count next to an appointment word.
_APPOINTMENT_WORDS = ("अपॉइंटमेंट", "अपॉइन्टमेंट", "समय", "तारीख", "ಅಪಾಯಿಂಟ್‌ಮೆಂಟ್", "ಅಪಾಯಿಂಟ್ಮೆಂಟ್", "ಸಮಯ", "ದಿನಾಂಕ")

# Words that end a booking in progress ("never mind").
_STOP_WORDS = {
    "cancel", "stop", "quit", "exit", "nevermind", "abort", "skip",
    "रद्द", "रुको", "छोड़ो", "ಬೇಡ", "ನಿಲ್ಲಿಸಿ",
}
_STOP_PHRASES = ("never mind", "forget it", "leave it", "no thanks", "no thank you", "रहने दो", "छोड़ दो", "ಬಿಡಿ")


def detect_change_intent(text: str) -> str | None:
    """"cancel", "reschedule", "ask" (could be either) or None."""
    lowered = " ".join((text or "").lower().split())
    if not lowered or _NEGATED.search(lowered):
        return None

    cancel = bool(_CANCEL_EN.search(lowered)) or any(token in lowered for token in _CANCEL_OTHER)
    reschedule = bool(_RESCHEDULE_EN.search(lowered)) or (
        any(token in lowered for token in _RESCHEDULE_OTHER)
        and any(word in lowered for word in _APPOINTMENT_WORDS)
    )
    if cancel and reschedule:
        return "ask"
    if reschedule:
        return "reschedule"
    if cancel:
        return "cancel"
    return None


def wants_to_stop(text: str) -> bool:
    """A short "never mind" / "cancel" said in the middle of a booking."""
    lowered = " ".join((text or "").lower().replace("'", "").split())
    if not lowered or any(ch.isdigit() for ch in lowered) or len(lowered.split()) > 4:
        return False
    return bool(set(re.findall(r"\w+", lowered)) & _STOP_WORDS) or any(p in lowered for p in _STOP_PHRASES)
