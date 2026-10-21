"""Live medicines: the medicines a doctor says during the visit, ready for the Prescription card.

While Live Assist listens, this keeps a list of the medicines the DOCTOR told the patient to take, with the
morning / noon / night times they named. The page copies new entries into the prescription DRAFT as rows marked
"heard, please verify". Nothing here signs or sends anything: the doctor edits and signs, as always.

The model reads the conversation, then the code checks it, so the model cannot invent things:
* a medicine whose name was not heard in the conversation is dropped;
* a strength, a number of days or a "how often" that has no matching words in the conversation is dropped;
* the morning / noon / night ticks come from the WORDS ("twice a day", "1-0-1", "at night"), not from the model;
* a value the doctor did not say stays empty. Nothing is ever filled in from "usual" doses.
"""
import re
import threading

from services.scribe import medication, summarize

# the clock times a tick stands for (the prescription's dose reminders run on clock times)
CLOCK = {"morning": "09:00", "noon": "14:00", "night": "21:00"}
SLOTS = tuple(CLOCK)

WINDOW_CHARS = 4000        # how much of the latest conversation the model reads
CANCEL_LOOK_CHARS = 1500

# Words that mean medicines may have been talked about. A pass of the model is skipped without one.
_CUE = re.compile(
    r"\b(?:prescrib\w*|tablets?|tabs?|capsules?|caps?|syrups?|injections?|ointments?|creams?|drops?|gels?|"
    r"inhalers?|sachets?|mg|mcg|ml|milligrams?|dose|doses|dosage|medicines?|medications?|drugs?|"
    r"take|taking|give|giving|write|writing|continue|stop|avoid|skip|discontinue|instead|no need|not needed|not required|times|daily|twice|thrice|once|morning|noon|afternoon|evening|night|bedtime|"
    r"before food|after food|before meals?|after meals?|\d\s*-\s*\d\s*-\s*\d|od|bd|tds|bid|tid|sos)\b",
    re.I)

_NUMBER_WORDS = re.compile(
    r"\b(?:one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|fifteen|twenty|thirty|forty|fifty|"
    r"sixty|seventy|eighty|ninety|hundred|thousand|half|quarter|single|week|weeks|month|months|fortnight|couple)\b",
    re.I)
_TIMING = re.compile(
    r"\b(?:once|twice|thrice|daily|every|morning|noon|afternoon|evening|night|bedtime|times|hourly|weekly|"
    r"\d\s*-\s*\d\s*-\s*\d|od|bd|tds|bid|tid|qid|hours?|needed|sos|breakfast|lunch|dinner|sleep\w*)\b", re.I)
_FORM_WORDS = re.compile(r"\b(?:tablets?|tabs?|capsules?|caps?|syrups?|injections?|ointments?|creams?|drops?)\b", re.I)

_STOP_BEFORE = re.compile(
    r"(?:stop|do not|don'?t|dont|avoid|no need(?: for| to take)?|not to take|not take|discontinue|skip|"
    r"instead of|leave off|without)\W+(?:\w+\W+){0,3}$", re.I)
_STOP_AFTER = re.compile(r"^\W*(?:is |are )?(?:not needed|not required|no longer|not necessary|stop\w*)", re.I)

SYSTEM_PROMPT = (
    "You listen to a consultation between a doctor and a patient and keep the list of medicines the DOCTOR has "
    "told the patient to take. The text may be machine-translated from Kannada or Hindi, so the spelling of a "
    "medicine name can be slightly off.\n\n"
    "Rules:\n"
    "1. Include ONLY medicines the doctor tells the patient to take or use. Never include a medicine the patient "
    "says they already take or took, unless the doctor tells them to continue it. Never suggest a medicine yourself.\n"
    "2. Use ONLY what is said. If the strength, form, dose, how often, food timing or number of days was not said, "
    "use null. Never fill in usual or typical values.\n"
    "3. If the doctor changes a medicine (\"make it once a day\", \"take it for 7 days instead\"), give its LATEST "
    "version. If the doctor cancels a medicine (\"don't take X\", \"stop X\"), leave it out.\n"
    "4. Keep each medicine name as spoken, written in English letters.\n"
    "5. The Doctor/Patient labels were guessed automatically and may be wrong; go by the meaning of the sentences.\n\n"
    "Reply with ONLY a JSON object, no other text: "
    '{"medicines": [ {"drug_name": string, "strength": string or null (e.g. "500 mg"), "form": string or null '
    '(tablet, capsule, syrup...), "dose_text": string or null (e.g. "1 tablet"), "frequency_text": string or null '
    '(as said, e.g. "twice a day", "1-0-1", "every morning", "at night"), "food": "before" | "after" | "with" | "any", '
    '"duration_days": integer or null, "as_needed": true or false, "instructions": string or null} ]}. '
    'If the doctor has prescribed nothing, reply {"medicines": []}.'
)

_FIELDS = ("drug_name", "strength", "form", "dose_text", "frequency_text", "food", "duration_days",
           "as_needed", "instructions", "times")


# --------------------------------------------------------------------------------------------
# small pure helpers
# --------------------------------------------------------------------------------------------

def key_of(name: str) -> str:
    """The same medicine said two ways ("Pantoprazole", "pantoprazole tablet") has the same key."""
    words = re.findall(r"[a-z]+", _FORM_WORDS.sub(" ", (name or "").lower()))
    return "".join(words) or re.sub(r"[^a-z0-9]", "", (name or "").lower())


def times_from(text: str) -> list[str]:
    """Morning / noon / night ticks from words. "twice a day" and "1-0-1" give morning + night."""
    lowered = (text or "").lower()
    pattern = re.search(r"(?<![\d.])([01])\s*[-–]\s*([01])\s*[-–]\s*([01])(?![\d.])", lowered)
    if pattern:
        return [slot for slot, bit in zip(SLOTS, pattern.groups()) if bit == "1"]
    named = set()
    if re.search(r"\b(?:morning|breakfast|wake\w*)\b", lowered):
        named.add("morning")
    if re.search(r"\b(?:noon|afternoon|lunch|midday)\b", lowered):
        named.add("noon")
    if re.search(r"\b(?:night|bedtime|bed time|dinner|evening|sleep\w*)\b", lowered):
        named.add("night")
    if named:
        return [slot for slot in SLOTS if slot in named]
    if re.search(r"\b(?:thrice|tds|tid)\b|\b(?:three|3)\s*times\b", lowered):
        return list(SLOTS)
    if re.search(r"\b(?:twice|bd|bid)\b|\b(?:two|2)\s*times\b", lowered):
        return ["morning", "night"]
    return []                    # "once a day" with no time named: the doctor chooses


def dose_times_for(times: list[str]) -> list[str]:
    return [CLOCK[slot] for slot in SLOTS if slot in times]


def _number_said(value, conversation: str) -> bool:
    """A strength or a number of days must have some number in the conversation to come from."""
    digits = re.findall(r"\d+", str(value or ""))
    if not digits:
        return True
    return any(d in conversation for d in digits) or bool(_NUMBER_WORDS.search(conversation))


def _cancelled(name: str, recent: str) -> bool:
    """True if the doctor just said not to take this medicine ("don't take Dolo", "stop the cream")."""
    words = [w for w in re.findall(r"[a-z]+", (name or "").lower()) if len(w) >= 3 and not _FORM_WORDS.fullmatch(w)]
    if not words:
        return False
    lowered = recent.lower()
    for match in re.finditer(re.escape(words[0]), lowered):
        before = lowered[max(0, match.start() - 60):match.start()]
        after = lowered[match.end():match.end() + 40]
        if _STOP_BEFORE.search(before) or _STOP_AFTER.search(after):
            return True
    return False


_UNITS = (
    (re.compile(r"\b(?:milli\s?grams?|milligrammes?|mgs?)\b", re.I), "mg"),
    (re.compile(r"\b(?:micro\s?grams?|mcgs?)\b", re.I), "mcg"),
    (re.compile(r"\b(?:milli\s?lit(?:re|er)s?|mls?)\b", re.I), "ml"),
)


def tidy_units(text):
    """"40 milligrams" and "500mg" are both written "40 mg" / "500 mg"."""
    if not text:
        return text
    for pattern, short in _UNITS:
        text = pattern.sub(short, text)
    return re.sub(r"(\d)\s*(mg|mcg|ml)\b", r"\1 \2", text).strip()


def has_cue(text: str) -> bool:
    return bool(_CUE.search(text or ""))


# --------------------------------------------------------------------------------------------
# reading the conversation
# --------------------------------------------------------------------------------------------

def _transcript(session) -> str:
    labels = {"doctor": "Doctor", "patient": "Patient"}
    with session.lock:
        return "\n".join(f"{labels.get(t['speaker'], 'Speaker')}: {t['text']}" for t in session.turns)


def _checked(raw_items: list[dict], conversation: str) -> list[dict]:
    items = [n for n in (medication.normalise(m) for m in raw_items) if n][:medication.MAX_MEDICINES]
    items, dropped = medication.keep_only_what_was_said(items, conversation)
    if dropped:
        print(f"[LiveMedicines] dropped (name not heard): {dropped}")
    for item in items:
        item["strength"] = tidy_units(item["strength"])
        item["dose_text"] = tidy_units(item["dose_text"])
        if item["dose_text"] and item["dose_text"] == item["strength"]:
            item["dose_text"] = None                     # "500 mg" is the strength, not the dose ("1 tablet")
        if item["strength"] and not _number_said(item["strength"], conversation):
            item["strength"] = None
        if item["duration_days"] and not _number_said(item["duration_days"], conversation):
            item["duration_days"] = None
        if item["frequency_text"] and not (_TIMING.search(item["frequency_text"]) and _TIMING.search(conversation)):
            item["frequency_text"] = None                # not a "how often" (e.g. "5 days"), or never said
        item["times"] = [] if item["as_needed"] else times_from(f"{item['frequency_text'] or ''} {item['instructions'] or ''}")
    return items


def _entry(item: dict, rev: int) -> dict:
    return {
        "key": key_of(item["drug_name"]), "rev": rev, "removed": False,
        **{field: item.get(field) for field in _FIELDS},
    }


def _merge(session, items: list[dict], recent: str) -> bool:
    """Fold a model reading into the session's list. Returns True if anything the page shows changed."""
    changed = False
    seen = set()
    for item in items:
        key = key_of(item["drug_name"])
        if not key:
            continue
        seen.add(key)
        old = session.medicines.get(key)
        if old is None or old["removed"]:
            session.med_rev += 1
            session.medicines[key] = _entry(item, session.med_rev)
            changed = True
            continue
        updated = dict(old)
        for field in _FIELDS:
            new = item.get(field)
            if field == "as_needed":
                updated[field] = bool(new)
            elif field == "food":
                if new and new != "any":
                    updated[field] = new
            elif field == "drug_name":
                updated[field] = old[field]                 # the name first heard is kept
            elif new not in (None, "", []):
                updated[field] = new                          # a value is never erased by a later reading
        if {f: updated[f] for f in _FIELDS} != {f: old[f] for f in _FIELDS}:
            session.med_rev += 1
            updated["rev"] = session.med_rev
            session.medicines[key] = updated
            changed = True
    # The model leaving a medicine out is not enough to remove it (a model can slip); the doctor must have said so.
    for key, old in list(session.medicines.items()):
        if key not in seen and not old["removed"] and _cancelled(old["drug_name"], recent):
            session.med_rev += 1
            session.medicines[key] = {**old, "removed": True, "rev": session.med_rev}
            changed = True
    return changed


def update(session, chat=None) -> bool:
    """One reading of the conversation so far. Never raises. Returns True if the medicine list changed."""
    try:
        with session.med_lock:                       # one reading at a time (the model call is slow; the page's polls are not held up)
            transcript = _transcript(session)
            fresh = transcript[session.med_checked:]        # only NEW talk wakes the model (it reads the whole tail)
            if not has_cue(fresh):
                session.med_checked = len(transcript)
                return False

            with session.lock:
                known = [m for m in session.medicines.values() if not m["removed"]]
            noted = "\n".join(
                f"- {m['drug_name']} {m['strength'] or ''} {m['frequency_text'] or ''}".strip() for m in known) or "(none yet)"
            reply = (chat or summarize.call_chat)([
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": (
                    f"Medicines already noted (keep them unless the doctor changes or cancels them):\n{noted}\n\n"
                    f"Conversation so far (latest part):\n{transcript[-WINDOW_CHARS:]}")},
            ])
            items = _checked(medication.parse_medicines(reply), transcript)
            with session.lock:
                changed = _merge(session, items, transcript[-CANCEL_LOOK_CHARS:])
                session.med_checked = len(transcript)
            return changed
    except (summarize.SummaryError, medication.MedicationError) as exc:
        print(f"[LiveMedicines] {getattr(session, 'consultation_id', '?')}: no reading this time ({exc})")
    except Exception as exc:  # noqa: BLE001 - a live helper must never take anything else down
        print(f"[LiveMedicines] {getattr(session, 'consultation_id', '?')}: error {exc!r}")
    return False


def public_unlocked(session) -> list[dict]:
    """What the page receives: every entry, oldest change first. `missing` lists what was not said.

    The caller holds session.lock (the snapshot does).
    """
    out = []
    for entry in sorted((dict(m) for m in session.medicines.values()), key=lambda e: e["rev"]):
        times = entry.get("times") or []
        missing = []
        if not entry["removed"]:
            if not entry["as_needed"] and not times:
                missing.append("timing")
            if not entry["as_needed"] and not entry.get("duration_days"):
                missing.append("days")
        out.append({**entry, "epoch": session.med_epoch, "dose_times": [] if entry["as_needed"] else dose_times_for(times), "missing": missing})
    return out


def public(session) -> list[dict]:
    with session.lock:
        return public_unlocked(session)
