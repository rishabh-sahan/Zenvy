"""Live Assist: short suggestions for the DOCTOR while a consultation is going on.

The model reads the conversation so far and may propose a few cards. Code - not the prompt - decides what
the doctor actually sees:

* every card must be grounded: its quoted evidence has to appear in the conversation (or, for a history
  card, in the earlier-visit notes), so the model cannot invent what was said;
* cards that name a medicine or a dose, order a test, name a disease, or state a diagnosis are dropped;
* repeats of anything already shown or dismissed are dropped;
* a small set of built-in red-flag rules fires even if the model says nothing (a safety net).

Cards are suggestions only: this module never writes to a note, signs, or books anything.
"""
import json
import re

from services.scribe import summarize

KINDS = ("ask", "red_flag", "history", "wrap_up", "consider")
MAX_CARDS_PER_RUN = 4
MIN_TEXT, MAX_TEXT = 8, 220
MAX_TRANSCRIPT_CHARS = 4000
DUPLICATE_OVERLAP = 0.7           # share of words two cards may have in common before one is a repeat

SYSTEM_PROMPT = (
    "You are a quiet assistant sitting beside a doctor during a live consultation. You read the "
    "conversation so far and suggest a FEW short cards for the DOCTOR only (never for the patient).\n\n"
    "Card kinds:\n"
    '  "ask"      - a question the doctor may want to ask next (something important that is missing)\n'
    '  "red_flag" - something the patient said that may need urgent attention\n'
    '  "history"  - a fact from the patient\'s EARLIER visits listed below that matters now\n'
    '  "wrap_up"  - something not covered yet (e.g. how long to continue, when to come back)\n'
    '  "consider" - a gentle thing to keep in mind. It must start with "Consider" or "Keep in mind".\n\n'
    "Rules:\n"
    "1. English only, one short sentence per card (at most 25 words). The conversation below is always English text, "
    "but it may have been machine-translated from Kannada or Hindi speech, so the wording can be slightly odd: "
    "judge it by what it says and quote it exactly as written.\n"
    "2. Use ONLY what was said in the conversation or in the earlier visits. Never invent facts.\n"
    "3. Each card needs \"evidence\": a short exact quote (at most 12 words) from the conversation, or the "
    "date of the earlier visit for a history card.\n"
    "4. NEVER state or imply a diagnosis. NEVER name a disease, a medicine, a dose or a test. NEVER give "
    "treatment advice. The doctor decides everything.\n"
    "5. Do not repeat a card that is listed under ALREADY SHOWN.\n"
    "6. If nothing useful can be said right now, return an empty list.\n\n"
    'Reply with ONLY a JSON object: {"cards": [{"kind": "...", "text": "...", "evidence": "..."}]}'
)

# --------------------------------------------------------------------------------------------
# what the doctor must never be shown
# --------------------------------------------------------------------------------------------

_DOSE = re.compile(
    r"\b\d+(?:\.\d+)?\s*(?:mg|mcg|µg|g|gm|ml|iu|units?|tablets?|tabs?|caps?|capsules?|drops?|puffs?)\b", re.I)
_TREATMENT = re.compile(
    r"\b(?:prescribe\w*|dosage|dose of|tablets?|capsules?|syrup|injection|ointment|antibiotics?|painkillers?|"
    r"steroids?|antacids?|inhaler|take \w+ (?:twice|thrice|once|daily))\b", re.I)
_DRUGS = re.compile(
    r"\b(?:paracetamol|acetaminophen|ibuprofen|aspirin|amoxicillin|azithromycin|ciprofloxacin|cetirizine|"
    r"metformin|insulin|atorvastatin|amlodipine|omeprazole|pantoprazole|diclofenac|dolo|crocin|calpol|"
    r"prednisolone|salbutamol|warfarin|clopidogrel|losartan|telmisartan|levothyroxine|doxycycline)\b", re.I)
_TESTS = re.compile(
    r"\b(?:blood tests?|urine tests?|lab tests?|cbc|x-?rays?|mri|ct scans?|ecg|ekg|ultrasound|ultrasonography|"
    r"biopsy|hba1c|endoscopy|angiogram|culture test|get tested|order a)\b", re.I)
_DISEASES = re.compile(
    r"\b(?:diabetes|diabetic|hypertension|dengue|malaria|typhoid|pneumonia|tuberculosis|\btb\b|cancer|covid|"
    r"heart attack|myocardial|stroke|appendicitis|asthma|migraine|meningitis|sepsis|anemia|anaemia|jaundice|"
    r"hepatitis|bronchitis|arthritis|epilepsy|depression|anxiety disorder|kidney failure|thyroid)\b", re.I)
_DIAGNOSTIC = re.compile(
    r"\b(?:patient is suffering|suffering from|diagnos(?:is|ed|e)|likely has|probably has|"
    r"it is (?:a|an)|this is (?:a|an)|confirmed|definitely|certainly)\b", re.I)

_URGENT = re.compile(r"\b(?:urgent\w*|emergenc\w*|immediate\w*|right away|serious\w*|severe\w*|life[- ]threatening)\b", re.I)

# Words that make a symptom worth an alarm. "May need urgent attention" alone does not: a model once called
# "sleeping only four hours" a red flag with exactly those words.
_ALARM = re.compile(
    r"\b(?:severe\w*|worst|sudden\w*|unbearable|terrible|excruciating|bleed\w*|blood\w*|faint\w*|unconscious\w*|"
    r"collaps\w*|seizure\w*|convuls\w*|breath\w*|chest|heart|numb\w*|paraly\w*|slurred|confus\w*|drowsy|pale|bluish|"
    r"cannot|can'?t|unable|bent|deform\w*|swell\w*|suicid\w*|self[- ]harm|palpitat\w*|vomit\w*|dizz\w*)\b", re.I)

# Evidence made only of these ("Sorry, Doctor.", "Okay doctor", "Hello") is a greeting or a mis-heard word, not
# something worth interrupting the doctor for. (Found live: "okay doctor" was heard as "Sorry, Doctor" and the
# model made a card about the patient apologising.)
_CHATTER = {
    "sorry", "doctor", "hello", "hi", "okay", "ok", "yes", "yeah", "thanks", "thank", "please", "good", "morning",
    "afternoon", "evening", "alright", "sir", "madam", "fine", "sure", "right", "well", "hmm", "you",
}


def has_substance(evidence: str) -> bool:
    return any(len(w) >= 3 and w not in _CHATTER for w in _words(evidence))

_BANNED = (
    ("dose", _DOSE), ("treatment", _TREATMENT), ("medicine name", _DRUGS), ("test", _TESTS),
    ("disease name", _DISEASES), ("diagnosis", _DIAGNOSTIC),
)


def violations(text: str) -> list[str]:
    """Why this card text must not be shown to the doctor ([] = fine)."""
    return [name for name, pattern in _BANNED if pattern.search(text or "")]


# --------------------------------------------------------------------------------------------
# built-in red-flag rules (English wording; the model covers the other languages)
# --------------------------------------------------------------------------------------------

RED_FLAG_RULES = (
    ("chest_pain", re.compile(r"\bchest (?:pain|tightness|pressure)\b|\bpain in (?:my |the )?(?:chest|heart)\b|\bheart pain\b|\bchest (?:is |was )?(?:paining|hurting)\b|\bchest hurts\b|\btightness in (?:my |the )?chest\b", re.I), "Chest pain"),
    ("breathing", re.compile(r"\b(?:difficult(?:y|ies)?|hard|trouble|struggl\w*)(?: in| to| with)? breath(?:e|ing)\b|\bbreathing (?:difficult(?:y|ies)|problems?|trouble)\b|\bunable to breathe\b|\bshort(?:ness)? of breath\b|\bbreathless\w*\b|\bcan'?t (?:even )?breathe\b|\bgasping\b", re.I), "Difficulty breathing"),
    ("fainting", re.compile(r"\bfaint(?:ed|ing)\b|\bpassed out\b|\bunconscious\w*\b|\bblack(?:ed|ing) out\b|\b(?:lost|loss of) consciousness\b", re.I), "Fainting or loss of consciousness"),
    ("seizure", re.compile(r"\bseizures?\b|\bconvulsions?\b", re.I), "A seizure"),
    ("bleeding", re.compile(r"\b(?:heavy|severe|uncontrolled) bleeding\b|\bvomit(?:ing|ed)? blood\b|\bcough(?:ing|ed)? (?:up )?blood\b|\bblood in (?:my |the )?(?:stool|vomit)\b|\bbleeding (?:a lot|heavily|profusely)\b|\blot of (?:blood|bleeding)\b|\bbleeding (?:is |has )?(?:not|n't) stop\w*\b", re.I), "Heavy bleeding"),
    ("self_harm", re.compile(r"\bsuicid\w*\b|\bwant to die\b|\bend(?:ing)? my life\b|\bkill myself\b|\bharm myself\b", re.I), "Thoughts of self-harm"),
    ("stroke_signs", re.compile(r"\bslurred speech\b|\bspeech (?:is )?slurred\b|\bface (?:is )?droop\w*\b|\bweakness (?:on|in) one side\b|\bcan'?t move (?:my )?(?:arm|leg)\b", re.I), "Possible stroke signs"),
    ("severe_headache", re.compile(r"\bworst headache\b|\bsudden severe headache\b", re.I), "A sudden severe headache"),
    ("swelling", re.compile(r"\bswelling (?:of|in) (?:the |my )?(?:face|throat|tongue|lips)\b|\bthroat (?:is )?closing\b", re.I), "Swelling of the face or throat"),
)


_NEGATION = re.compile(
    r"\b(?:no|not|without|never|none|deny|denies|denied|nothing|n't|"
    r"(?:do|does|did|have|has|had|is|are|was|were)n'?t|(?:do|does|did|have|has|had|is|are|was|were) not)\b[^.?!]{0,25}$", re.I)


def _negated(text: str, start: int) -> bool:
    """"No chest pain" / "she does not have breathlessness": not a red flag."""
    return bool(_NEGATION.search(text[max(0, start - 40):start]))


_LEAD_IN = r"(?:(?:so|ok|okay|and|also|now|then|well|please|doctor|sir|madam)\b[, ]*)*"
_QUESTION_START = re.compile(
    r"^\s*" + _LEAD_IN + r"(?:any|do|does|did|have|has|had|are|is|was|were|can|could|would|will|how|what|when|"
    r"where|why|which|who|is there|are there|tell me (?:if|whether|about)|let me know (?:if|whether))\b", re.I)


def statements(text: str) -> str:
    """The conversation without the doctor's QUESTIONS in it.

    The doctor asking "Any chest pain?" is not the patient reporting chest pain, so the built-in red-flag
    rules must not see it. A question is recognised by how the sentence STARTS ("Any...", "Do you...",
    "Have you..."), not by a question mark: speech-to-text puts "?" on plain statements too ("I have chest
    pain since this morning?"), and missing a real red flag is worse than one extra alert.
    """
    kept = []
    text = re.sub(r"(?im)^\s*(?:doctor|patient|speaker)\s*:\s*", "", text or "")   # speaker labels
    for sentence in re.findall(r"[^.?!\n]+[.?!]?", text):
        sentence = sentence.strip()
        if not sentence or _QUESTION_START.match(sentence):
            continue
        kept.append(sentence)
    return " . ".join(kept)


def red_flag_cards(text: str, already_fired: set[str]) -> list[dict]:
    """Cards for red-flag phrases in `text` that have not fired yet. Mutates `already_fired`."""
    cards = []
    for key, pattern, label in RED_FLAG_RULES:
        if key in already_fired:
            continue
        match = next((m for m in pattern.finditer(text or "") if not _negated(text, m.start())), None)
        if match:
            already_fired.add(key)
            cards.append({
                "kind": "red_flag", "source": "rule",
                "text": f"{label} was mentioned - consider urgent evaluation.",
                "evidence": match.group(0)[:80],
            })
    return cards


# --------------------------------------------------------------------------------------------
# reading and checking the model's answer
# --------------------------------------------------------------------------------------------

def _words(value: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", (value or "").lower())


def parse_cards(reply: str) -> list[dict]:
    text = re.sub(r"<think>.*?</think>", "", reply or "", flags=re.S).strip()
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.M).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("not a JSON object")
    data = json.loads(text[start:end + 1])
    cards = data.get("cards") if isinstance(data, dict) else None
    if not isinstance(cards, list):
        raise ValueError("no card list")
    return [c for c in cards if isinstance(c, dict)]


def is_grounded(evidence: str, *sources: str) -> bool:
    """The quoted evidence really occurs in the conversation (allowing for small wording differences)."""
    needed = [w for w in _words(evidence) if len(w) >= 3]
    if not needed:
        return False
    haystack = set()
    for source in sources:
        haystack.update(_words(source))
    found = sum(1 for w in needed if w in haystack)
    return found / len(needed) >= 0.75


_FILLER = {
    "ask", "the", "about", "their", "patient", "whether", "any", "recently", "has", "have", "had", "and", "for",
    "with", "that", "this", "does", "did", "was", "were", "are", "you", "your", "how", "what", "when", "which",
    "also", "consider", "keep", "mind", "check", "not", "yet", "been", "doctor", "discussed", "mentioned",
}


def _content_words(text: str) -> set[str]:
    return {w for w in _words(text) if len(w) >= 3 and w not in _FILLER}


def is_repeat(text: str, others: list[str]) -> bool:
    """Is this about the same thing as a card already shown? (Compares meaningful words only.)"""
    mine = _content_words(text)
    if not mine:
        return True
    for other in others:
        theirs = _content_words(other)
        if theirs and len(mine & theirs) / min(len(mine), len(theirs)) >= DUPLICATE_OVERLAP:
            return True
    return False


def check_cards(cards: list[dict], transcript: str, history_text: str, shown: list[str]) -> tuple[list[dict], dict]:
    """The cards that may be shown, and why the others were dropped (counts by reason)."""
    kept: list[dict] = []
    dropped: dict[str, int] = {}

    def drop(reason: str) -> None:
        dropped[reason] = dropped.get(reason, 0) + 1

    for card in cards:
        kind = str(card.get("kind") or "").strip().lower()
        text = " ".join(str(card.get("text") or "").split())
        evidence = " ".join(str(card.get("evidence") or "").split())
        if kind not in KINDS:
            drop("unknown_kind")
            continue
        if not (MIN_TEXT <= len(text) <= MAX_TEXT):
            drop("bad_length")
            continue
        if violations(text):
            drop("unsafe:" + violations(text)[0])
            continue
        # a history card quotes the earlier visits; every other card (except wrap-up, which is about what
        # was NOT said) must quote the conversation itself
        source = history_text if kind == "history" else transcript
        if kind != "wrap_up" and not is_grounded(evidence, source):
            drop("not_grounded")
            continue
        if kind not in ("wrap_up", "history") and not has_substance(evidence):
            drop("weak_evidence")
            continue
        if kind == "red_flag" and not (_URGENT.search(text) and _ALARM.search(_URGENT.sub(" ", text) + " " + evidence)):
            kind = "ask" if re.match(r"^ask\b", text, re.I) else "consider"      # do not cry wolf
        if kind == "consider" and not re.match(r"^(consider|keep in mind)\b", text, re.I):
            text = "Keep in mind: " + text[0].lower() + text[1:]
        if is_repeat(text, shown + [k["text"] for k in kept]):
            drop("repeat")
            continue
        kept.append({"kind": kind, "text": text, "evidence": evidence[:200] or None, "source": "model"})
        if len(kept) >= MAX_CARDS_PER_RUN:
            break
    return kept, dropped


def build_messages(transcript: str, history_lines: list[str], shown: list[str]) -> list[dict]:
    history = "\n".join(f"- {line}" for line in history_lines) or "(none)"
    already = "\n".join(f"- {line}" for line in shown[-12:]) or "(none)"
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": (
            f"CONVERSATION SO FAR:\n{transcript[-MAX_TRANSCRIPT_CHARS:]}\n\n"
            f"EARLIER VISITS WITH THIS DOCTOR:\n{history}\n\n"
            f"ALREADY SHOWN (do not repeat):\n{already}")},
    ]


def generate(transcript: str, history_lines: list[str], shown: list[str], chat=None) -> tuple[list[dict], dict]:
    """Ask the model, then keep only the cards that pass every check.

    Raises summarize.SummaryError if the model could not be reached or answered with nothing usable.
    """
    chat = chat or summarize.call_chat
    reply = chat(build_messages(transcript, history_lines, shown))
    try:
        raw = parse_cards(reply)
    except (ValueError, json.JSONDecodeError) as exc:
        raise summarize.SummaryError("live_assist_bad_reply", f"The model's reply was not usable: {exc}") from exc
    return check_cards(raw, transcript, "\n".join(history_lines), shown)
