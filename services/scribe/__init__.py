"""Ambient consultation scribe: recording -> transcript -> English note.

audio.py       convert / split a recording into speech segments
transcribe.py  speech-to-text per segment + doctor/patient labelling
summarize.py   English four-section note from the transcript (Sarvam chat)
team_c.py      client for Team C's consultation API
pipeline.py    runs the steps and reports progress to Team C
"""
