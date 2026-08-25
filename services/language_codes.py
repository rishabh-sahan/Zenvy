"""
Shared language-code mapping used across services and scripts, so
"kn-IN" -> "kn" (Sarvam's STT language_code -> our internal short code
used by the LLM client and TTS service) isn't duplicated in multiple
places.
"""

LANGUAGE_CODE_TO_SHORT = {
    "kn-IN": "kn",
    "hi-IN": "hi",
    "en-IN": "en",
}