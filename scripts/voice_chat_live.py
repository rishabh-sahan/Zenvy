"""
Live conversational voice bot (mic in, speaker out), single-turn per
exchange, repeated in a loop.

Reuses the exact same STT -> LLM -> TTS pipeline as
voice_chat_via_services.py -- the only new piece here is capturing
audio from your microphone and playing the reply back through your
speakers, instead of reading/writing pre-recorded WAV files.

Controls:
  - Press ENTER to start recording
  - Press ENTER again to stop recording (this is your turn to speak)
  - Type 'q' + ENTER instead of recording, to quit

Requires packages not yet in requirements.txt:
    pip install sounddevice numpy scipy
(sounddevice needs a working audio backend -- it uses your OS's
default input/output devices, no extra setup needed on Windows.)
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np
import sounddevice as sd
from scipy.io.wavfile import write as write_wav, read as read_wav

from services.llm.client import generate_reply
from scripts.voice_chat_via_services import (
    transcribe_via_service,
    synthesize_via_service,
    LANGUAGE_CODE_TO_SHORT,
)

SAMPLE_RATE = 16000  # standard for speech; STT service accepts any valid WAV
MIC_INPUT_PATH = "scripts/mic_input.wav"
REPLY_OUTPUT_PATH = "scripts/live_reply.wav"


def record_until_enter() -> np.ndarray:
    """Record from the default microphone until the user presses ENTER."""
    frames = []

    def callback(indata, frames_count, time_info, status):
        frames.append(indata.copy())

    stream = sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="int16", callback=callback)
    with stream:
        input()  # blocks here until ENTER; callback keeps appending frames meanwhile

    if not frames:
        return np.array([], dtype="int16")
    return np.concatenate(frames, axis=0)


def play_wav(path: str) -> None:
    """Play a WAV file through the default speaker/output device."""
    rate, data = read_wav(path)
    sd.play(data, rate)
    sd.wait()  # block until playback finishes, so turns don't overlap


def run_live_loop() -> None:
    print("Zenvy voice bot -- live mic mode")
    print("Press ENTER to start recording, ENTER again to stop. Type 'q' + ENTER to quit.\n")

    while True:
        user_in = input("Press ENTER to speak (or 'q' to quit): ")
        if user_in.strip().lower() == "q":
            print("Goodbye.")
            break

        print("Recording... press ENTER to stop.")
        audio = record_until_enter()
        if audio.size == 0:
            print("No audio captured, try again.\n")
            continue

        write_wav(MIC_INPUT_PATH, SAMPLE_RATE, audio)

        try:
            stt_result = transcribe_via_service(MIC_INPUT_PATH)
        except Exception as e:
            print(f"[STT] failed: {e}\n")
            continue

        transcript = stt_result["text"]
        lang_code = stt_result["language_code"]
        print(f"Heard ({lang_code}): {transcript}")

        short_lang = LANGUAGE_CODE_TO_SHORT.get(lang_code)
        if short_lang is None:
            print(f"[!] Unsupported language code: {lang_code}\n")
            continue

        try:
            reply_text = generate_reply(transcript, short_lang)
        except Exception as e:
            print(f"[LLM] failed: {e}\n")
            continue
        print(f"Reply ({short_lang}): {reply_text}")

        try:
            synthesize_via_service(reply_text, short_lang, REPLY_OUTPUT_PATH)
        except Exception as e:
            print(f"[TTS] failed: {e}\n")
            continue

        print("Playing reply...")
        play_wav(REPLY_OUTPUT_PATH)
        print()


if __name__ == "__main__":
    run_live_loop()