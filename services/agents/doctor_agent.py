"""The doctor agent: a text assistant in the doctor's dashboard.

It can look things up for THIS doctor (their schedule, what waits for their approval, updates from the
coordinator, their own earlier visits with a patient) and prepare DRAFTS (a prescription from the
transcript, a follow-up date). It never approves a note, signs a prescription or books anything: every
draft waits for the doctor. Every call carries the doctor's own token, so Team C decides what is visible.

Requests are read by rules first (fast, predictable); a small model call classifies the rest. The reply
is always built from real data; the model is used only to summarise earlier visits, with a plain list
as the fallback if it fails.
"""
import json
import re
from datetime import date, datetime, timedelta, timezone

from services.scribe import medication, summarize
from services.scribe.team_c import TeamC, TeamCError

IST = timezone(timedelta(hours=5, minutes=30))

INTENTS = ("schedule", "pending", "updates", "history", "draft_prescription", "draft_follow_up", "help")

HELP = (
    "I can help with: your schedule today, what is waiting for your approval, new updates, a summary of "
    "this patient's earlier visits with you, a draft prescription from the recording, and a draft follow-up "
    "date (for example 'follow up in 2 weeks'). I only prepare drafts - you review and sign everything."
)


def detect_intent(message: str) -> str | None:
    text = " ".join((message or "").lower().split())
    if not text:
        return None
    has = lambda *words: any(w in text for w in words)  # noqa: E731
    if has("prescription", "medicine", "medication", "medicines") and has("draft", "prepare", "write", "extract", "suggest", "create", "make"):
        return "draft_prescription"
    if has("follow up", "follow-up", "followup", "come back", "review in") and (
        has("draft", "set", "add", "schedule", "book", "plan", "suggest") or _follow_up_days(text)
    ):
        return "draft_follow_up"
    if has("history", "summar", "earlier visit", "previous visit", "last visit", "past visit", "earlier", "before"):
        return "history"
    if has("pending", "approval", "approve", "waiting", "to sign", "unsigned", "to do", "todo"):
        return "pending"
    if has("update", "what's new", "whats new", "new message", "notification", "anything new", "alerts"):
        return "updates"
    if has("schedule", "today", "appointments", "who is next", "who's next", "my patients", "agenda", "list"):
        return "schedule"
    if has("help", "what can you do", "what can i ask"):
        return "help"
    return None


def classify_with_model(message: str, chat=None) -> str:
    """The rules did not recognise it: ask the model to pick ONE of the known intents."""
    chat = chat or summarize.call_chat
    try:
        reply = chat([
            {"role": "system", "content": (
                "Pick the one intent that best matches the doctor's message. Reply with ONLY one word from: "
                + ", ".join(INTENTS) + ". Use 'help' if none fits.")},
            {"role": "user", "content": message},
        ])
    except Exception as exc:  # noqa: BLE001
        print(f"[DoctorAgent] Could not classify: {exc}")
        return "help"
    word = re.sub(r"<think>.*?</think>", "", reply or "", flags=re.S).strip().lower()
    word = re.sub(r"[^a-z_]", "", word.split()[-1] if word.split() else "")
    return word if word in INTENTS else "help"


# ---------------------------------------------------------------------------
# small readers
# ---------------------------------------------------------------------------

_NUMBER_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "ten": 10, "twelve": 12, "fourteen": 14}
_UNITS = {"day": 1, "days": 1, "week": 7, "weeks": 7, "month": 30, "months": 30}


def _follow_up_days(text: str) -> int | None:
    """"in 2 weeks", "after 10 days", "next week", "a month" -> days. Only time-based requests count."""
    text = text.lower()
    if re.search(r"\bnext\s+week\b", text):
        return 7
    if re.search(r"\b(?:a|one)\s+fortnight\b", text):
        return 14
    match = re.search(r"\b(?:in|after|within)?\s*(\d{1,3}|" + "|".join(_NUMBER_WORDS) + r"|a|an)\s+(days?|weeks?|months?)\b", text)
    if not match:
        return None
    count = match.group(1)
    count = 1 if count in ("a", "an") else int(count) if count.isdigit() else _NUMBER_WORDS[count]
    days = count * _UNITS[match.group(2)]
    return days if 1 <= days <= 365 else None


def _local(iso: str) -> datetime:
    return datetime.fromisoformat(iso).astimezone(IST)


def _line(appointment: dict) -> str:
    when = _local(appointment["appointment_datetime"]).strftime("%H:%M")
    bits = [appointment.get("patient_label", "Patient")]
    if appointment.get("appointment_type") == "follow_up":
        bits.append("follow-up")
    state = []
    if appointment.get("consent_state") and appointment["consent_state"] != "none":
        state.append(f"recording {appointment['consent_state']}")
    if appointment.get("note_status"):
        state.append(f"note {appointment['note_status']}")
    if appointment.get("prescription_status"):
        state.append(f"prescription {appointment['prescription_status']}")
    return f"{when} - {' '.join(bits)}" + (f" ({', '.join(state)})" if state else "")


# ---------------------------------------------------------------------------
# the skills
# ---------------------------------------------------------------------------

def _schedule(team_c: TeamC, today: date) -> dict:
    todays = [a for a in team_c.doctor_appointments() if _local(a["appointment_datetime"]).date() == today]
    if not todays:
        return {"reply": "You have no appointments today.", "refresh": []}
    return {"reply": f"Today you have {len(todays)} appointment(s):\n" + "\n".join(_line(a) for a in todays), "refresh": []}


def _pending(team_c: TeamC) -> dict:
    waiting = []
    for appointment in team_c.doctor_appointments():
        reasons = []
        if appointment.get("note_status") == "draft":
            reasons.append("note waiting for your approval")
        if appointment.get("prescription_status") == "draft":
            reasons.append("prescription waiting for your signature")
        if reasons:
            when = _local(appointment["appointment_datetime"]).strftime("%a %d %b %H:%M")
            waiting.append(f"{when} - {appointment.get('patient_label', 'Patient')}: {' and '.join(reasons)}")
    if not waiting:
        return {"reply": "Nothing is waiting for your approval.", "refresh": []}
    return {"reply": "Waiting for you:\n" + "\n".join(waiting), "refresh": []}


def _updates(team_c: TeamC) -> dict:
    unread = team_c.messages(unread=True)
    if not unread:
        return {"reply": "No new updates.", "refresh": []}
    team_c.mark_messages_read([m["message_id"] for m in unread])
    return {"reply": f"{len(unread)} update(s):\n" + "\n".join(f"- {m['text']}" for m in unread), "refresh": ["updates"]}


def _history(team_c: TeamC, appointment_id: str | None, chat=None) -> dict:
    if not appointment_id:
        return {"reply": "Open the appointment first, then ask me for the history.", "refresh": []}
    visits = team_c.patient_history(appointment_id)
    if not visits:
        return {"reply": "You have no earlier recorded visits with this patient.", "refresh": []}
    facts = "\n".join(
        f"{v['date']}: complaint: {v['chief_complaint'] or '-'}; assessment: {v['assessment'] or '-'}; "
        f"plan: {v['plan'] or '-'}; medicines: {', '.join(v['medicines']) or '-'}"
        for v in visits
    )
    try:
        reply = (chat or summarize.call_chat)([
            {"role": "system", "content": (
                "Summarise these earlier visits of one patient for their doctor in at most 4 short sentences, oldest "
                "to newest. Use ONLY the facts given. Do not add advice, diagnoses or anything not listed.")},
            {"role": "user", "content": facts},
        ])
        reply = re.sub(r"<think>.*?</think>", "", reply or "", flags=re.S).strip()
    except Exception as exc:  # noqa: BLE001
        print(f"[DoctorAgent] Could not summarise, listing instead: {exc}")
        reply = ""
    if not reply:
        reply = "Earlier visits:\n" + "\n".join(f"- {line}" for line in facts.splitlines())
    return {"reply": reply, "refresh": [], "visits": len(visits)}


def _draft_prescription(team_c: TeamC, consultation_id: str | None, chat=None) -> dict:
    if not consultation_id:
        return {"reply": "Start the consultation first, then ask me to draft the prescription.", "refresh": []}
    detail = team_c.get_consultation(consultation_id)
    notes = detail.get("notes") or []
    plan = notes[-1].get("plan", "") if notes else ""
    try:
        items, dropped = medication.extract_medications(detail.get("turns") or [], plan, chat=chat)
    except medication.MedicationError as exc:
        reply = ("There is no transcript to read the medicines from yet." if exc.code == "no_transcript"
                 else "I could not read the medicines just now. You can add them yourself in the Prescription card.")
        return {"reply": reply, "refresh": []}
    if not items:
        return {"reply": "I did not find any medicine prescribed in the recording. You can add them yourself in the "
                         "Prescription card.", "refresh": []}
    team_c.put_prescription(consultation_id, items, "transcript")
    names = ", ".join(i["drug_name"] for i in items)
    note = f" I left out {', '.join(dropped)} because it was not in the recording." if dropped else ""
    return {"reply": f"I drafted {len(items)} medicine(s): {names}.{note} Please check each line and fill in anything "
                     "missing, then sign - nothing is sent to the patient until you do.",
            "refresh": ["prescription"], "medicines": len(items)}


def _draft_follow_up(team_c: TeamC, consultation_id: str | None, appointment_id: str | None, message: str) -> dict:
    days = _follow_up_days(message)
    if not consultation_id or not appointment_id:
        return {"reply": "Start the consultation first, then ask me for a follow-up.", "refresh": []}
    if days is None:
        return {"reply": "Tell me when, for example 'follow up in 2 weeks' or 'follow up next week'.", "refresh": []}
    visit = next((a for a in team_c.doctor_appointments() if a["appointment_id"] == appointment_id), None)
    if visit is None:
        return {"reply": "I could not find this appointment.", "refresh": []}
    start = _local(visit["appointment_datetime"])
    target = start.date() + timedelta(days=days)
    team_c.put_follow_up(consultation_id, target.isoformat(), start.strftime("%H:%M"))
    return {"reply": f"I set a follow-up for {target.strftime('%a %d %b')} at {start.strftime('%H:%M')} ({days} days after this visit). "
                     "It is booked when you approve the note, or press 'Book it now'.",
            "refresh": ["followup"]}


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------

def handle(
    message: str, token: str, appointment_id: str | None = None, consultation_id: str | None = None,
    team_c: TeamC | None = None, chat=None, today: date | None = None,
) -> dict:
    """{"reply": str, "intent": str, "refresh": [...]}. Raises TeamCError if Team C refuses (e.g. not your patient)."""
    team_c = team_c or TeamC(token)
    intent = detect_intent(message) or classify_with_model(message, chat)
    today = today or datetime.now(IST).date()

    if intent == "schedule":
        result = _schedule(team_c, today)
    elif intent == "pending":
        result = _pending(team_c)
    elif intent == "updates":
        result = _updates(team_c)
    elif intent == "history":
        result = _history(team_c, appointment_id, chat)
    elif intent == "draft_prescription":
        result = _draft_prescription(team_c, consultation_id, chat)
    elif intent == "draft_follow_up":
        result = _draft_follow_up(team_c, consultation_id, appointment_id, message)
    else:
        intent, result = "help", {"reply": HELP, "refresh": []}

    team_c.record_action(f"assistant_{intent}", f"{intent} done", appointment_id=appointment_id)
    return {"intent": intent, **result}
