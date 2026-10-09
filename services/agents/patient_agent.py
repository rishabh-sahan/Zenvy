"""The patient agent's medicine skills: read back what the doctor signed, say when the next dose is,
and note that a dose was taken.

It only repeats what the doctor signed. It never suggests, changes or explains a medicine, and a draft
prescription is invisible to it (Team C only returns signed ones).
"""
import re
from datetime import datetime, timedelta, timezone

import requests

from services import conversation_client
from services.conversation_client import NotLoggedIn
from services.orchestrator.templates import render_template

IST = timezone(timedelta(hours=5, minutes=30))

_MEDICINE = r"(?:medicine|medicines|medication|medications|meds|tablet|tablets|pill|pills|capsule|capsules|syrup|dose|doses)"
_TAKEN = re.compile(
    rf"\b(?:i\s+(?:have\s+|just\s+|already\s+)?(?:took|taken|had|ate|swallowed)|(?:took|taken)\s+my|(?:have|had)\s+taken)\b.{{0,30}}\b{_MEDICINE}\b"
    rf"|\b{_MEDICINE}\b.{{0,20}}\b(?:taken|done)\b"
)
_NEXT = re.compile(
    rf"\bnext\s+(?:dose|{_MEDICINE})\b|\bwhen\s+(?:do|should|must|can)\s+i\s+(?:take|have)\b.{{0,25}}\b{_MEDICINE}\b"
    rf"|\bwhen\b.{{0,20}}\bnext\b.{{0,15}}\b{_MEDICINE}\b"
)
_LIST = re.compile(
    rf"\b(?:my|prescribed|prescription|doctor'?s?)\b.{{0,25}}\b{_MEDICINE}\b"
    rf"|\bmy\s+prescriptions?\b"
    rf"|\bwhat\b.{{0,15}}\b(?:do\s+i\s+take|am\s+i\s+taking|should\s+i\s+be\s+taking)\b"
    rf"|\b{_MEDICINE}\s+(?:list|schedule)\b"
)
_FOR_A_PROBLEM = re.compile(rf"\b{_MEDICINE}\s+for\b")     # "medicine for fever" is a question for a doctor, not a lookup

_OTHER_TAKEN = ("दवा ले ली", "दवाई ले ली", "गोली खा ली", "दवा खा ली", "ಔಷಧ ತೆಗೆದುಕೊಂಡೆ", "ಮಾತ್ರೆ ತೆಗೆದುಕೊಂಡೆ", "ಔಷಧ ತೆಗೆದುಕೊಂಡಿದ್ದೇನೆ", "ಮಾತ್ರೆ ತೆಗೆದುಕೊಂಡಿದ್ದೇನೆ")
_OTHER_NEXT = ("अगली खुराक", "अगली दवा", "दवा कब लेनी", "ಮುಂದಿನ ಡೋಸ್", "ಮುಂದಿನ ಔಷಧ")
_OTHER_LIST = ("मेरी दवा", "मेरी दवाइयाँ", "मेरी दवाइयां", "कौन सी दवा", "कौन-सी दवा", "ನನ್ನ ಔಷಧ", "ನನ್ನ ಮಾತ್ರೆ")


def detect_medicine_intent(text: str) -> str | None:
    """"taken", "next", "list" or None. Cautious: "medicine for fever" is not a lookup."""
    lowered = " ".join((text or "").lower().replace("’", "'").split())
    if not lowered:
        return None
    if _TAKEN.search(lowered) or any(p in lowered for p in _OTHER_TAKEN):
        return "taken"
    if _NEXT.search(lowered) or any(p in lowered for p in _OTHER_NEXT):
        return "next"
    if (_LIST.search(lowered) and not _FOR_A_PROBLEM.search(lowered)) or any(p in lowered for p in _OTHER_LIST):
        return "list"
    return None


def _name(item: dict) -> str:
    return " ".join(part for part in (item.get("drug_name"), item.get("strength")) if part)


def _food(item: dict) -> str:
    return {"before": "before food", "after": "after food", "with": "with food"}.get(item.get("food"), "")


def describe(item: dict) -> str:
    """"Paracetamol 500 mg (1 tablet, 08:00 and 21:00, after food)" - exactly what the doctor wrote."""
    if item.get("as_needed"):
        when = "only when needed"
    else:
        times = item.get("dose_times") or []
        when = " and ".join(times) if len(times) < 3 else ", ".join(times[:-1]) + " and " + times[-1]
    parts = [p for p in (item.get("dose_text"), when, _food(item)) if p]
    return f"{_name(item)} ({', '.join(parts)})" if parts else _name(item)


def _when(iso: str | None, now: datetime | None = None) -> str:
    if not iso:
        return ""
    local = datetime.fromisoformat(iso).astimezone(IST)
    today = (now or datetime.now(IST)).astimezone(IST).date()
    clock = local.strftime("%H:%M")
    if local.date() == today:
        return f"today at {clock}"
    if local.date() == today + timedelta(days=1):
        return f"tomorrow at {clock}"
    return f"on {local.strftime('%a %d %b')} at {clock}"


def _items(medications: dict) -> list[dict]:
    return [item for prescription in medications.get("prescriptions", []) for item in prescription.get("items", [])]


def handle(auth_id: str | None, short_lang: str, intent: str, now: datetime | None = None) -> str:
    """The reply to a medicine question or "I took it"."""
    if not auth_id:
        return render_template("MED_NOT_LOGGED_IN", short_lang)
    try:
        if intent == "taken":
            result = conversation_client.mark_my_doses_taken(auth_id)
            conversation_client.record_agent_action("patient", "mark_dose_taken", f"{result.get('taken', 0)} dose(s)", auth_id=auth_id)
            if not result.get("taken"):
                return render_template("MED_TAKEN_NONE", short_lang)
            return render_template("MED_TAKEN", short_lang, medicines=", ".join(dict.fromkeys(result.get("medicines") or [])) or "your medicine")

        items = _items(conversation_client.get_my_medications(auth_id))
        conversation_client.record_agent_action("patient", f"medicines_{intent}", f"{len(items)} medicine(s)", auth_id=auth_id)
        if not items:
            return render_template("MED_NONE", short_lang)
        if intent == "next":
            upcoming = [i for i in items if i.get("next_dose_at")]
            if not upcoming:
                return render_template("MED_NEXT_NONE", short_lang)
            soonest = min(upcoming, key=lambda i: i["next_dose_at"])
            return render_template(
                "MED_NEXT", short_lang, medicine=_name(soonest), dose=soonest.get("dose_text") or "1 dose",
                when=_when(soonest["next_dose_at"], now),
            )
        return render_template("MED_LIST", short_lang, medicines="; ".join(describe(i) for i in items))
    except NotLoggedIn:
        return render_template("MED_NOT_LOGGED_IN", short_lang)
    except requests.exceptions.RequestException as exc:
        print(f"[PatientAgent] Team C unreachable: {exc}")
        return render_template("MED_FAILED", short_lang)
