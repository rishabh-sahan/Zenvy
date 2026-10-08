"""Speech-to-text for each speech segment, and doctor/patient labelling.

Labelling is a heuristic on the PAUSES, not speaker recognition (Roadmap Day 92:
"if true diarization isn't available, guess from pause length and turn-taking").
The doctor usually speaks first; a long pause means the other person is now
talking; a short pause means the same person is still going. It is wrong
sometimes, which is why the doctor can correct any line in the review screen.
"""
import os
import time
from concurrent.futures import ThreadPoolExecutor

import requests

from services.scribe.audio import Segment

STT_URL = os.getenv("STT_URL", "http://127.0.0.1:8001/transcribe")
STT_TIMEOUT_SECONDS = 60
STT_ATTEMPTS = 3
STT_WORKERS = 3

# A pause at least this long is treated as the other person taking the turn.
TURN_CHANGE_PAUSE_SECONDS = 0.9


class SttError(Exception):
    """Speech-to-text failed for a segment, even after retrying."""


def transcribe_segment(segment: Segment, stt_url: str | None = None, post=requests.post) -> dict:
    """Transcribe one segment: {"text": ..., "language_code": ...}."""
    url = stt_url or STT_URL
    last_error = "unknown error"
    for attempt in range(1, STT_ATTEMPTS + 1):
        try:
            response = post(
                url,
                files={"file": ("segment.wav", segment.wav, "audio/wav")},
                timeout=STT_TIMEOUT_SECONDS,
            )
        except requests.exceptions.RequestException as exc:
            last_error = f"request failed: {exc}"
        else:
            if response.status_code == 200:
                data = response.json()
                return {
                    "text": (data.get("text") or "").strip(),
                    "language_code": data.get("language_code"),
                }
            last_error = f"HTTP {response.status_code}"
            if response.status_code < 500:
                break  # our request is wrong; trying again will not help
        if attempt < STT_ATTEMPTS:
            time.sleep(attempt)  # 1 s, then 2 s
    raise SttError(last_error)


def transcribe_segments(
    segments: list[Segment], stt_url: str | None = None, post=requests.post, workers: int = STT_WORKERS
) -> list[dict]:
    """Transcribe all segments (a few at a time), keeping them in order."""
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        return list(pool.map(lambda s: transcribe_segment(s, stt_url, post), segments))


def label_turns(segments: list[Segment], results: list[dict]) -> list[dict]:
    """Turn segments + their text into doctor/patient turns.

    Segments with no text (noise) are dropped. Consecutive segments by the same
    guessed speaker are joined into one turn.
    """
    turns: list[dict] = []
    speaker = "doctor"
    previous_end: float | None = None

    for segment, result in zip(segments, results):
        text = (result.get("text") or "").strip()
        if not text:
            continue
        pause = segment.start - previous_end if previous_end is not None else 0.0
        if turns and pause >= TURN_CHANGE_PAUSE_SECONDS:
            speaker = "patient" if speaker == "doctor" else "doctor"
        previous_end = segment.end

        if turns and turns[-1]["speaker"] == speaker:
            turns[-1]["text"] += " " + text
            turns[-1]["end_seconds"] = round(segment.end, 2)
        else:
            turns.append(
                {
                    "speaker": speaker,
                    "text": text,
                    "start_seconds": round(segment.start, 2),
                    "end_seconds": round(segment.end, 2),
                    "language_code": result.get("language_code"),
                }
            )
    return turns


def dominant_language(turns: list[dict]) -> str | None:
    """The language that was spoken for the longest time, e.g. 'hi-IN'."""
    seconds: dict[str, float] = {}
    for turn in turns:
        code = turn.get("language_code")
        if code:
            length = (turn.get("end_seconds") or 0) - (turn.get("start_seconds") or 0)
            seconds[code] = seconds.get(code, 0.0) + max(length, 0.1)
    return max(seconds, key=seconds.get) if seconds else None
