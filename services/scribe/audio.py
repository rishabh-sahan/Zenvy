"""Audio handling for consultation recordings.

Browsers record WebM/Opus; the scribe needs 16 kHz mono WAV. Long recordings are
cut into speech segments at the pauses, because the speech-to-text API only
takes short clips. The pauses are also what the doctor/patient labelling uses.

Pure Python on purpose: audioop is gone in Python 3.13+, numpy is not installed.
"""
import array
import io
import subprocess
import tempfile
import wave
from dataclasses import dataclass
from pathlib import Path

SAMPLE_RATE = 16000
WINDOW_SECONDS = 0.02            # energy is measured in 20 ms steps
STRIDE = 4                       # look at every 4th sample: 4x faster, same result
MERGE_GAP_SECONDS = 0.6          # shorter pauses stay inside one segment
MIN_SPEECH_SECONDS = 0.15        # shorter blips are noise, not speech
MAX_SEGMENT_SECONDS = 25.0       # the speech-to-text API takes short clips
PREFERRED_CUT_WINDOW_SECONDS = 5.0
PAD_SECONDS = 0.15               # context kept around each segment
MAX_RECORDING_SECONDS = 60 * 60  # one hour
MIN_LOUD_RMS = 150               # below this the whole file is effectively silent


class AudioError(Exception):
    """The recording cannot be read or is not usable."""


@dataclass
class Segment:
    start: float            # seconds from the start of the recording
    end: float
    gap_before: float       # silence between the previous segment and this one
    wav: bytes              # this stretch as a standalone 16 kHz mono WAV


def to_wav_16k_mono(data: bytes, suffix: str = ".webm") -> bytes:
    """Convert any browser recording to 16 kHz mono 16-bit WAV with ffmpeg."""
    if not data:
        raise AudioError("The recording is empty.")
    suffix = suffix if suffix.startswith(".") else "." + suffix
    source = target = None
    try:
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as handle:
            handle.write(data)
            source = handle.name
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as handle:
            target = handle.name
        try:
            result = subprocess.run(
                ["ffmpeg", "-y", "-i", source, "-vn", "-ar", str(SAMPLE_RATE), "-ac", "1",
                 "-sample_fmt", "s16", target],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=300,
            )
        except FileNotFoundError as exc:
            raise AudioError("Audio conversion is not available on the server.") from exc
        except subprocess.TimeoutExpired as exc:
            raise AudioError("The recording took too long to convert.") from exc
        if result.returncode != 0:
            raise AudioError("The file could not be read as audio.")
        converted = Path(target).read_bytes()
        if len(converted) <= 44:
            raise AudioError("The recording contains no audio.")
        return converted
    finally:
        for name in (source, target):
            if name:
                Path(name).unlink(missing_ok=True)


def _read_wav(wav_bytes: bytes) -> tuple[array.array, int]:
    try:
        with wave.open(io.BytesIO(wav_bytes), "rb") as handle:
            if handle.getsampwidth() != 2 or handle.getnchannels() != 1:
                raise AudioError("Expected 16-bit mono audio.")
            rate = handle.getframerate()
            samples = array.array("h")
            samples.frombytes(handle.readframes(handle.getnframes()))
    except (wave.Error, EOFError) as exc:
        raise AudioError("The recording is not a readable WAV file.") from exc
    return samples, rate


def wav_seconds(wav_bytes: bytes) -> float:
    samples, rate = _read_wav(wav_bytes)
    return len(samples) / rate


def _wav_bytes(samples: array.array, rate: int) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(samples.tobytes())
    return buffer.getvalue()


def _window_energies(samples: array.array, rate: int) -> list[float]:
    """RMS of each 20 ms window (looking at every STRIDE-th sample)."""
    size = int(rate * WINDOW_SECONDS)
    energies = []
    for start in range(0, len(samples) - size + 1, size):
        chunk = samples[start:start + size:STRIDE]
        energies.append((sum(v * v for v in chunk) / len(chunk)) ** 0.5)
    return energies


def _speech_threshold(energies: list[float]) -> float | None:
    """Energy above which a window counts as speech; None if it is all silence."""
    if not energies:
        return None
    ordered = sorted(energies)
    loud = ordered[int(len(ordered) * 0.95)]
    if loud < MIN_LOUD_RMS:
        return None
    floor = ordered[int(len(ordered) * 0.20)]
    threshold = max(floor * 2.5, loud * 0.08, MIN_LOUD_RMS * 0.5)
    # Never above half the loudness: with no pauses at all (continuous speech)
    # the "floor" is as loud as the speech and would otherwise hide all of it.
    return min(threshold, loud * 0.5)


def _speech_runs(energies: list[float], threshold: float) -> list[list[int]]:
    """[first_window, last_window] runs of speech, with short pauses bridged."""
    runs: list[list[int]] = []
    bridge = int(MERGE_GAP_SECONDS / WINDOW_SECONDS)
    for index, value in enumerate(energies):
        if value <= threshold:
            continue
        if runs and index - runs[-1][1] <= bridge:
            runs[-1][1] = index
        else:
            runs.append([index, index])
    minimum = int(MIN_SPEECH_SECONDS / WINDOW_SECONDS)
    return [run for run in runs if run[1] - run[0] + 1 >= minimum]


def _cap_length(run: list[int], energies: list[float]) -> list[list[int]]:
    """Cut a run longer than MAX_SEGMENT_SECONDS, at its quietest point near the limit."""
    limit = int(MAX_SEGMENT_SECONDS / WINDOW_SECONDS)
    look_back = int(PREFERRED_CUT_WINDOW_SECONDS / WINDOW_SECONDS)
    parts = []
    start, end = run
    while end - start + 1 > limit:
        window_end = start + limit
        window_start = max(start + 1, window_end - look_back)
        cut = min(range(window_start, window_end), key=lambda i: energies[i])
        parts.append([start, cut])
        start = cut + 1
    parts.append([start, end])
    return parts


def split_on_speech(wav_bytes: bytes) -> list[Segment]:
    """Cut a recording into speech segments (each at most MAX_SEGMENT_SECONDS).

    Returns [] if the recording holds no speech at all. Raises AudioError if the
    file is unreadable or longer than an hour.
    """
    samples, rate = _read_wav(wav_bytes)
    total_seconds = len(samples) / rate
    if total_seconds > MAX_RECORDING_SECONDS:
        raise AudioError("The recording is longer than one hour.")

    energies = _window_energies(samples, rate)
    threshold = _speech_threshold(energies)
    if threshold is None:
        return []

    window = int(rate * WINDOW_SECONDS)
    pad = int(PAD_SECONDS * rate)
    segments: list[Segment] = []
    previous_end = 0.0
    for run in _speech_runs(energies, threshold):
        for first, last in _cap_length(run, energies):
            start_s = first * WINDOW_SECONDS
            end_s = (last + 1) * WINDOW_SECONDS
            clip = samples[max(0, first * window - pad): min(len(samples), (last + 1) * window + pad)]
            segments.append(
                Segment(start=start_s, end=end_s, gap_before=max(0.0, start_s - previous_end), wav=_wav_bytes(clip, rate))
            )
            previous_end = end_s
    return segments
