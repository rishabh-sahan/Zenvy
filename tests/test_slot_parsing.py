"""Times and dates read by rules (the safety net for the language model)."""
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest

from services.orchestrator.slot_parsing import parse_date, parse_time

TODAY = date(2026, 10, 9)  # a Friday


# ---------------------------------------------------------------------------
# times
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("said,expected", [
    # what failed in a real session
    ("11 am", "11:00"),
    ("11:00 AM", "11:00"),
    # other ways of saying the same
    ("11 AM.", "11:00"),
    ("at 11 am", "11:00"),
    ("11 a.m.", "11:00"),
    ("11a.m", "11:00"),
    ("eleven am", "11:00"),
    ("11 o'clock", "11:00"),
    ("11 oclock", "11:00"),
    ("eleven o'clock", "11:00"),
    ("3 pm", "15:00"),
    ("3 p.m.", "15:00"),
    ("three pm", "15:00"),
    ("3:30 pm", "15:30"),
    ("3.30 pm", "15:30"),
    ("12 pm", "12:00"),
    ("12 am", "00:00"),
    ("10:30", "10:30"),
    ("10.30", "10:30"),
    ("14:30", "14:30"),
    ("at 10:30 please", "10:30"),
    ("ten thirty", "10:30"),
    ("ten forty five", "10:45"),
    ("half past ten", "10:30"),
    ("half past two", "14:30"),
    ("quarter past nine", "09:15"),
    ("quarter to three", "14:45"),
    ("noon", "12:00"),
    ("at noon", "12:00"),
    ("11 in the morning", "11:00"),
    ("3 in the afternoon", "15:00"),
    ("5 in the evening", "17:00"),
    ("morning 9 am", "09:00"),
    ("3 o'clock", "15:00"),          # a clinic is not open at 3 in the night
    ("९ बजे", "09:00"),              # Devanagari digit
    ("११ बजे", "11:00"),
    ("दोपहर 2 बजे", "14:00"),
])
def test_clear_times_are_understood(said, expected):
    assert parse_time(said) == expected


@pytest.mark.parametrize("said,expected", [
    ("11", "11:00"),
    ("9", "09:00"),
    ("12", "12:00"),
    ("3", "15:00"),
    ("at 11", "11:00"),
    ("around 4", "16:00"),
    ("eleven", "11:00"),
    ("at eleven", "11:00"),
    ("11 please", "11:00"),
    ("14", "14:00"),
])
def test_a_bare_number_is_a_time_only_right_after_we_asked_for_one(said, expected):
    assert parse_time(said, bare_ok=True) == expected
    assert parse_time(said, bare_ok=False) is None  # not when we did not ask


@pytest.mark.parametrize("said", [
    "",
    "yes",
    "tomorrow",
    "I want to see a doctor",
    "9th of October",                 # a date, not 9 o'clock
    "10 october",
    "october 10",
    "12.10.2026",                     # a date written with dots, not 12:10
    "12/10/2026",
    "5/6",
    "2026-10-10",
    "I need 2 appointments",          # not the whole answer
    "number 2",
    "25:00",
    "13 pm",
    "0 am",
    "11:75",
    "my phone is 9876543210",
])
def test_things_that_are_not_a_time_are_left_alone(said):
    assert parse_time(said, bare_ok=True) is None


def test_a_date_and_a_time_in_one_sentence_give_the_time():
    assert parse_time("tomorrow at 10:30") == "10:30"
    assert parse_time("10 october at 3 pm") == "15:00"
    assert parse_time("9th of October at 11 am", bare_ok=True) == "11:00"


# ---------------------------------------------------------------------------
# dates
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("said,expected", [
    ("tomorrow", "2026-10-10"),
    ("Tomorrow.", "2026-10-10"),
    ("tmrw", "2026-10-10"),
    ("tommorow", "2026-10-10"),
    ("kal", "2026-10-10"),
    ("कल", "2026-10-10"),
    ("ನಾಳೆ", "2026-10-10"),
    ("today", "2026-10-09"),
    ("आज", "2026-10-09"),
    ("ಇಂದು", "2026-10-09"),
    ("day after tomorrow", "2026-10-11"),
    ("परसों", "2026-10-11"),
    ("saturday", "2026-10-10"),
    ("monday", "2026-10-12"),
    ("next monday", "2026-10-12"),
    ("on tue", "2026-10-13"),
    ("friday", "2026-10-16"),        # today is Friday: the same weekday means next week
    ("10 october", "2026-10-10"),
    ("10th of October", "2026-10-10"),
    ("10 oct 2026", "2026-10-10"),
    ("october 12", "2026-10-12"),
    ("Oct 12th", "2026-10-12"),
    ("october 12, 2026", "2026-10-12"),
    ("5 january", "2027-01-05"),     # already past this year: next year
    ("1 oct", "2027-10-01"),
    ("15/10", "2026-10-15"),
    ("15-10-2026", "2026-10-15"),
    ("15/10/26", "2026-10-15"),
    ("tomorrow at 10:30", "2026-10-10"),
    ("book it for 3rd november", "2026-11-03"),
])
def test_clear_dates_are_understood(said, expected):
    assert parse_date(said, TODAY) == expected


@pytest.mark.parametrize("said", [
    "",
    "yes",
    "11 am",
    "10:30",
    "my friend",                      # not Friday
    "next month",                     # not Monday
    "the monitor is broken",
    "I am satisfied",                 # not Saturday
    "sunny weather",
    "31 february",
    "45/13",
    "may I book",                     # the verb, not the month
    "I need 2 appointments",
])
def test_things_that_are_not_a_date_are_left_alone(said):
    assert parse_date(said, TODAY) is None


def test_the_year_rolls_over_for_a_date_that_already_passed():
    assert parse_date("8 october", TODAY) == "2027-10-08"
    assert parse_date("9 october", TODAY) == "2026-10-09"   # today itself is fine
