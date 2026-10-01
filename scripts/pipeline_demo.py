"""
Team B pipeline demo.

  python scripts/pipeline_demo.py nlu
      Offline: runs the NLU stage on the questions below (no API key needed).

  python scripts/pipeline_demo.py live <audio.wav> [--target kn|hi|en]
      Full pipeline on real audio. Needs the STT service running on port 8001
      and SARVAM_API_KEY in .env (run by whoever holds the key).
"""
import sys
import json
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.stdout.reconfigure(encoding="utf-8")

from services.orchestrator.pipeline import run_nlu, run_pipeline, PipelineError

# Replace these with the questions your team already tested
# (see Team-B/test_results.csv, column "Sentence").
QUESTIONS = {
    "kn": [
        "ನನಗೆ ನಾಳೆ ಡಾಕ್ಟರ್ ಜೊತೆ ಅಪಾಯಿಂಟ್‌ಮೆಂಟ್ ಬೇಕು",
    ],
    "hi": [
        "मुझे कल सुबह डॉक्टर शर्मा के साथ अपॉइंटमेंट चाहिए",
    ],
    "en": [
        "I want to book an appointment with Dr. Sharma tomorrow at 10 AM",
        "What are the visiting hours?",
    ],
}


def cmd_nlu():
    for lang, questions in QUESTIONS.items():
        print(f"\n===== {lang} =====")
        for q in questions:
            print(f"\nQ: {q}")
            try:
                print(json.dumps(run_nlu(q), ensure_ascii=False, indent=2))
            except PipelineError as e:
                print("ERROR:", e)


def cmd_live(wav, target):
    try:
        out = run_pipeline(wav, target_lang=target)
        print(json.dumps(out, ensure_ascii=False, indent=2))
    except PipelineError as e:
        print("PIPELINE ERROR:", e)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="mode", required=True)
    sub.add_parser("nlu")
    live = sub.add_parser("live")
    live.add_argument("wav")
    live.add_argument("--target", choices=["kn", "hi", "en"])
    args = parser.parse_args()

    if args.mode == "nlu":
        cmd_nlu()
    else:
        cmd_live(args.wav, args.target)