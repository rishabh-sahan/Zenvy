"""Understanding times and dates by rules, as a safety net for the language model.

The language model that reads "11 am" or "tomorrow" is not reliable on short
answers: the same "11 am" can come back empty one minute and "11:00" the next,
and "11", "11 o'clock", "noon" or "10 october" often fail. A booking cannot
depend on that, so when the model finds nothing, these rules try.

They are deliberately cautious: they only return a value when the text clearly
says one, and return None otherwise (the bot then asks again).
"""
import re
import unicodedata
from datetime import date, timedelta

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

_NUMBER_WORDS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
    "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
}
_NUMBER_WORD_RE = "|".join(_NUMBER_WORDS)
_MINUTE_WORDS = {
    "fifteen": 15, "twenty": 20, "thirty": 30, "forty five": 45, "fortyfive": 45,
    "fifty": 50, "ten": 10, "five": 5,
}

_MORNING = ("morning", "subah", "सुबह", "ಬೆಳಿಗ್ಗೆ")
_AFTERNOON = ("afternoon", "evening", "night", "dopahar", "shaam", "दोपहर", "शाम", "रात", "ಮಧ್ಯಾಹ್ನ", "ಸಂಜೆ", "ರಾತ್ರಿ")

_MONTH_RE = (
    r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|"
    r"sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
)
_MONTHS = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6, "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12}

_WEEKDAY_RE = (
    r"(monday|mon|tuesday|tues|tue|wednesday|wed|thursday|thurs|thur|thu|"
    r"friday|fri|saturday|sat|sunday|sun)"
)
_WEEKDAYS = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}


def _ascii_digits(text: str) -> str:
    """Turn Devanagari / Kannada digits into 0-9."""
    return "".join(str(unicodedata.digit(ch)) if ch.isdigit() and ord(ch) > 127 else ch for ch in text)


def _clean(text: str) -> str:
    text = _ascii_digits((text or "").lower())
    text = text.replace("’", "'")
    text = re.sub(r"(?<![a-z])([ap])\.\s?m\.?", r"\1m", text)    # a.m. / p.m. (also glued: 11a.m)
    text = re.sub(r"(?<=\d)\s*([ap])\s?m\b", r" \1m", text)    # 11a m / 11 a m
    return " ".join(text.split())


def _words_to_digits(text: str) -> str:
    """'half past ten' -> '10:30', 'ten thirty' -> '10:30', 'quarter to three' -> '2:45'."""
    hour = rf"({_NUMBER_WORD_RE}|\d{{1,2}})"

    def to_int(word: str) -> int:
        return _NUMBER_WORDS[word] if word in _NUMBER_WORDS else int(word)

    text = re.sub(rf"\bhalf past {hour}\b", lambda m: f"{to_int(m.group(1))}:30", text)
    text = re.sub(rf"\bquarter past {hour}\b", lambda m: f"{to_int(m.group(1))}:15", text)
    text = re.sub(rf"\bquarter to {hour}\b", lambda m: f"{(to_int(m.group(1)) - 2) % 12 + 1}:45", text)
    minutes = "|".join(sorted((re.escape(w) for w in _MINUTE_WORDS), key=len, reverse=True))
    text = re.sub(
        rf"\b{hour}[\s-]+({minutes})\b",
        lambda m: f"{to_int(m.group(1))}:{_MINUTE_WORDS[m.group(2)]:02d}",
        text,
    )
    # "eleven am", "eleven o'clock"
    text = re.sub(
        rf"\b({_NUMBER_WORD_RE})\b(?=\s*(?:am|pm|o'?clock|oclock|baje|बजे|ಗಂಟೆ))",
        lambda m: str(_NUMBER_WORDS[m.group(1)]),
        text,
    )
    return text


def _to_24h(hour: int, minute: int, marker: str | None, hint: str | None, assume: bool) -> str | None:
    """Apply am/pm (or a morning/afternoon hint) and return 'HH:MM', or None if impossible."""
    if not (0 <= minute <= 59):
        return None
    if marker == "am":
        if not (1 <= hour <= 12):
            return None
        hour = hour % 12
    elif marker == "pm":
        if not (1 <= hour <= 12):
            return None
        hour = hour % 12 + 12
    elif hour > 23:
        return None
    elif hour <= 12 and hint == "pm":
        hour = hour % 12 + 12
    elif hour <= 12 and hint == "am":
        hour = hour % 12
    elif assume and 1 <= hour <= 7:
        hour += 12           # a clinic is not open at 3 in the morning: "3" means 3 pm
    # 8-11 stay as morning, 12 stays noon, 13-23 are already 24-hour times.
    return f"{hour:02d}:{minute:02d}"


# ---------------------------------------------------------------------------
# time
# ---------------------------------------------------------------------------

_PREFIX = r"(?:at|around|about|by|say|maybe|like|it'?s|its|is)?\s*"
_SUFFIX = r"\s*(?:please|sharp|only|ok|okay|sir|doctor)?$"
# a time must not be part of a longer number/date such as 12.10.2026 or 5/6
_NOT_IN_A_DATE_BEFORE = r"(?<![\d.:/-])"
_NOT_IN_A_DATE_AFTER = r"(?![.:/-]?\d)"


def parse_time(text: str, bare_ok: bool = False) -> str | None:
    """'HH:MM' (24-hour) if the text clearly gives a time of day, else None.

    A bare number such as "11" only counts when bare_ok is True, which the
    caller sets when it has just asked "what time?", and only if the number is
    the whole answer ("11", "at 11") - never "9th of October".
    """
    text = _words_to_digits(_clean(text))
    if not text:
        return None

    hint = None
    if any(word in text for word in _AFTERNOON):
        hint = "pm"
    elif any(word in text for word in _MORNING):
        hint = "am"

    if re.search(r"\b(noon|midday)\b", text):
        return "12:00"

    # 10:30, 10.30, 10:30 pm
    match = re.search(rf"{_NOT_IN_A_DATE_BEFORE}(\d{{1,2}})\s*[:.]\s*(\d{{2}})\s*(am|pm)?{_NOT_IN_A_DATE_AFTER}", text)
    if match:
        return _to_24h(int(match.group(1)), int(match.group(2)), match.group(3), hint, assume=True)

    # 11 am, 3 pm
    match = re.search(rf"{_NOT_IN_A_DATE_BEFORE}(\d{{1,2}})\s*(am|pm)\b", text)
    if match:
        return _to_24h(int(match.group(1)), 0, match.group(2), hint, assume=False)

    # 11 o'clock, 11 baje, 11 बजे
    match = re.search(rf"{_NOT_IN_A_DATE_BEFORE}(\d{{1,2}})\s*(?:o'?clock|oclock|baje|बजे|ಗಂಟೆ)", text)
    if match:
        return _to_24h(int(match.group(1)), 0, None, hint, assume=True)

    # "11 in the morning", "3 in the afternoon": a lone number with a part of the day
    numbers = re.findall(r"\d+", text)
    if hint and len(numbers) == 1 and not _looks_like_a_date(text):
        return _to_24h(int(numbers[0]), 0, None, hint, assume=False)

    # the whole answer is just a number (or number word): "11", "at eleven"
    if bare_ok:
        words = {str(v): str(v) for v in range(1, 24)} | {w: str(n) for w, n in _NUMBER_WORDS.items()}
        match = re.fullmatch(rf"{_PREFIX}(\d{{1,2}}|{_NUMBER_WORD_RE}){_SUFFIX}", text)
        if match:
            return _to_24h(int(words.get(match.group(1), match.group(1))), 0, None, None, assume=True)
    return None


def _looks_like_a_date(text: str) -> bool:
    return bool(
        re.search(r"\d(?:st|nd|rd|th)\b", text)
        or re.search(rf"\b{_MONTH_RE}\b", text)
        or re.search(r"\d{1,2}\s*[/-]\s*\d{1,2}", text)
    )


# ---------------------------------------------------------------------------
# date
# ---------------------------------------------------------------------------

_DAY_AFTER_TOMORROW = ("day after tomorrow", "day after tmrw", "parso", "परसों", "परसो")
_TOMORROW = ("tomorrow", "tomorow", "tommorow", "tmrw", "tmr", "kal", "कल", "ನಾಳೆ")
_TODAY = ("today", "aaj", "आज", "ಇಂದು")


def _has_word(text: str, words: tuple[str, ...]) -> bool:
    for word in words:
        if word.isascii():
            if re.search(rf"(?<![a-z]){re.escape(word)}(?![a-z])", text):
                return True
        elif word in text:
            return True
    return False


def _valid(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _resolve(day: int, month: int, year: str | None, today: date) -> str | None:
    if year:
        found = _valid(int(year), month, day)
    else:
        found = _valid(today.year, month, day)
        if found and found < today:
            found = _valid(today.year + 1, month, day)
    return found.isoformat() if found else None


def parse_date(text: str, today: date) -> str | None:
    """'YYYY-MM-DD' if the text clearly names a day, else None. `today` is the hospital's date."""
    text = _clean(text)
    if not text:
        return None

    if _has_word(text, _DAY_AFTER_TOMORROW):
        return (today + timedelta(days=2)).isoformat()
    if _has_word(text, _TOMORROW):
        return (today + timedelta(days=1)).isoformat()
    if _has_word(text, _TODAY):
        return today.isoformat()

    # 10 october, 10th of oct 2026
    match = re.search(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s*(?:of\s+)?{_MONTH_RE}\b\.?(?:\s*,?\s*(\d{{4}}))?", text)
    if match:
        return _resolve(int(match.group(1)), _MONTHS[match.group(2)[:3]], match.group(3), today)

    # october 10, oct 10th, october 10 2026
    match = re.search(rf"\b{_MONTH_RE}\b\.?\s*(\d{{1,2}})(?:st|nd|rd|th)?\b(?:\s*,?\s*(\d{{4}}))?", text)
    if match:
        return _resolve(int(match.group(2)), _MONTHS[match.group(1)[:3]], match.group(3), today)

    # 10/10, 10-10-2026 (day first, as in India)
    match = re.search(r"(?<![\d:])(\d{1,2})\s*[/-]\s*(\d{1,2})(?:\s*[/-]\s*(\d{2,4}))?(?!\d)", text)
    if match:
        year = match.group(3)
        if year and len(year) == 2:
            year = "20" + year
        return _resolve(int(match.group(1)), int(match.group(2)), year, today)

    # monday, next friday, on sat (the same weekday as today means next week)
    match = re.search(rf"\b{_WEEKDAY_RE}\b", text)
    if match:
        wanted = _WEEKDAYS[match.group(1)[:3]]
        return (today + timedelta(days=(wanted - today.weekday()) % 7 or 7)).isoformat()

    return None
