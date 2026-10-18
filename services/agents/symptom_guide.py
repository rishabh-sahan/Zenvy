"""The patient agent's symptom skills: which kind of doctor suits a symptom, and which doctors to suggest.

What it does, and what it deliberately does not:

* It reads a patient's own words ("I have a headache", "it did not improve", "which doctor should I see") in
  English, Hindi and Kannada and picks ONE of the specialties the hospital really has. It never diagnoses, never
  names a medicine, and never makes a doctor up: the doctors and their free times always come from Team C.
* A reviewable table of symptom words decides the specialty. Only when a message sounds like a health complaint
  and nothing in the table matches is the language model asked, and its answer is checked against the real list.
* Serious symptoms (chest pain, trouble breathing, fainting, heavy bleeding, ...) are flagged so the patient is
  first told to get urgent care. The same English rules the doctor's Live Assist uses are applied, plus short
  Hindi and Kannada word lists.
"""
import json
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from services.agents import live_assist

IST = timezone(timedelta(hours=5, minutes=30))

SPECIALTIES = (
    "Cardiologist", "General Physician", "Neurologist", "Orthopedist", "Dermatologist",
    "ENT Specialist", "Ophthalmologist", "Gynecologist", "Pediatrician", "Psychiatrist",
)

MAX_SUGGESTIONS = 3          # doctors read out to the patient
MAX_CANDIDATES = 8           # doctors whose free times are looked up
DAYS_AHEAD = 4               # today and the next three days

# ---------------------------------------------------------------------------------------------------
# symptom words -> specialty.  (specialty, English pattern, Hindi + Kannada words)
# On a tie the specialty that comes FIRST here wins, so "fever and a sore throat" is a General Physician.
# ---------------------------------------------------------------------------------------------------
_TABLE = (
    ("Cardiologist",
     r"\bpalpitation\w*|\bheart (?:racing|beating fast|problem|trouble|pain)\b|\bblood pressure\b|\b(?:high|low) bp\b|\bbp\b|"
     r"\bchest (?:pain|tightness|pressure|discomfort)\b|\bpain in (?:my |the )?(?:chest|heart)\b",
     ("दिल", "धड़कन", "ब्लड प्रेशर", "बीपी", "सीने में दर्द", "छाती में दर्द", "ಹೃದಯ", "ಎದೆ ಬಡಿತ", "ರಕ್ತದೊತ್ತಡ", "ಎದೆ ನೋವು", "ಎದೆನೋವು")),
    ("General Physician",
     r"\bfever\w*|\bcold\b|\bcough\w*|\brunny nose\b|\bstomach\w*|\babdominal\b|\btummy\b|\bbelly\b|\bvomit\w*|\bnausea\w*|\bdiarrh\w*|"
     r"\bloose motions?\b|\bconstipat\w*|\bacidity\b|\bindigestion\b|\bbody ?ache\w*|\bweakness\b|\bfeel(?:ing)? weak\b|\btired\w*|"
     r"\bfatigue\b|\bunwell\b|\bnot feeling (?:well|good)\b|\bsick\b|\bflu\b|\bchills\b|\bloss of appetite\b|\bjaundice\b",
     ("बुखार", "सर्दी", "जुकाम", "खांसी", "खाँसी", "पेट दर्द", "पेट में दर्द", "उल्टी", "दस्त", "कमज़ोरी", "कमजोरी", "थकान", "बदन दर्द", "बीमार",
      "ಜ್ವರ", "ನೆಗಡಿ", "ಕೆಮ್ಮು", "ಹೊಟ್ಟೆ ನೋವು", "ಹೊಟ್ಟೆನೋವು", "ವಾಂತಿ", "ಬೇಧಿ", "ಸುಸ್ತು", "ಆಯಾಸ", "ಮೈ ಕೈ ನೋವು", "ಅನಾರೋಗ್ಯ")),
    ("Neurologist",
     r"\bheadache\w*|\bhead ?ache\b|\bmigraine\w*|\bdizz\w*|\bvertigo\b|\bnumb(?:ness|ed|ing)?\b|\btingling\b|\bseizure\w*|\bconvulsion\w*|"
     r"\bhead (?:is )?(?:paining|spinning)\b|\bmemory (?:loss|problem)s?\b",
     ("सिरदर्द", "सिर दर्द", "सर दर्द", "सरदर्द", "सिर में दर्द", "माइग्रेन", "चक्कर", "सुन्न", "झनझनाहट", "दौरा", "मिर्गी",
      "ತಲೆನೋವು", "ತಲೆ ನೋವು", "ತಲೆ ಸುತ್ತು", "ಮೈಗ್ರೇನ್", "ಜೋಮು", "ಸೆಳವು", "ಫಿಟ್ಸ್")),
    ("Orthopedist",
     r"\bjoints?\b|\bknees?\b|\bback ?pain\b|\bbackache\b|\blower back\b|\bneck pain\b|\bshoulder\w*|\bfracture\w*|"
     r"\bbroken (?:bone|leg|arm|hand|wrist|finger|ankle|rib|foot|collarbone)s?\b|\bsprain\w*|\bswollen (?:ankle|wrist|knee|foot)\b|"
     r"\bbones?\b|\bmuscle (?:pain|ache|cramp)s?\b|\bhip pain\b|\bsciatica\b|\barthritis\b|\bsprained\b|"
     r"\b(?:pain|ache|aching|hurts?|hurting|swelling|swollen|stiff\w*)\b.{0,25}\b(?:hands?|arms?|legs?|foot|feet|ankles?|wrists?|elbows?|fingers?|thighs?|calf|calves|hips?|necks?|heels?)\b|"
     r"\b(?:hands?|arms?|legs?|foot|feet|ankles?|wrists?|elbows?|fingers?|thighs?|calf|calves|hips?|necks?|heels?)\b.{0,15}\b(?:pain|ache|aching|hurts?|hurting|swelling|swollen|stiff\w*)\b|"
     r"\bpain in (?:my |the )?(?:lower |upper )?back\b|\b(?:lower|upper) back\b",
     ("जोड़ों", "जोड़", "घुटने", "घुटना", "कमर दर्द", "कमर में दर्द", "पीठ दर्द", "पीठ में दर्द", "हड्डी", "फ्रैक्चर", "मोच", "कंधे",
      "हाथ में दर्द", "पैर में दर्द", "गर्दन में दर्द", "टांग में दर्द", "कलाई",
      "ಕೀಲು", "ಮೊಣಕಾಲು", "ಬೆನ್ನು ನೋವು", "ಬೆನ್ನುನೋವು", "ಮೂಳೆ", "ಮುರಿ", "ಭುಜ", "ಉಳುಕು", "ಕೈ ನೋವು", "ಕಾಲು ನೋವು", "ಕುತ್ತಿಗೆ ನೋವು", "ಕೈಯಲ್ಲಿ ನೋವು", "ಕಾಲಿನಲ್ಲಿ ನೋವು")),
    ("Dermatologist",
     r"\brash\w*|\bitch\w*|\bacne\b|\bpimple\w*|\bhair ?(?:fall|loss)\b|\bhair (?:is |are )?(?:falling|thinning)\b|\bbald\w*|\bskin\b|\beczema\b|\bhives\b|\bdandruff\b|\bboils?\b|"
     r"\bwarts?\b|\bfungal\b|\bblisters?\b|\bpsoriasis\b|\bsunburn\b",
     ("त्वचा", "खुजली", "दाने", "बाल झड़", "बाल गिर", "मुंहासे", "मुहांसे", "एलर्जी", "फोड़े", "ಚರ್ಮ", "ತುರಿಕೆ", "ಕೂದಲು ಉದುರ", "ಮೊಡವೆ", "ಅಲರ್ಜಿ", "ಗುಳ್ಳೆ")),
    ("ENT Specialist",
     r"\bear ?ache\b|\bear pain\b|\bpain in (?:my |the )?ear\b|\bsore throat\b|\bthroat (?:pain|infection|irritation)\b|\bsinus\w*|\btonsil\w*|"
     r"\bblocked nose\b|\bnose (?:is )?block\w*|\bnosebleed\w*|\bhearing\b|\bsnoring\b|\bhoarse\w*|\bvoice (?:loss|change)\b|\bear discharge\b",
     ("कान", "गला", "गले", "साइनस", "टॉन्सिल", "नाक बंद", "नाक", "ಕಿವಿ", "ಗಂಟಲು", "ಮೂಗು", "ಸೈನಸ್", "ಟಾನ್ಸಿಲ್")),
    ("Ophthalmologist",
     r"\beyes?\b.{0,20}\b(?:pain|painful|red|redness|itch\w*|water\w*|swollen|swelling|blurr?\w*|vision|sore|burning)\b|\bblurr?ed vision\b|"
     r"\bvision\b|\bred eyes?\b|\bwatery eyes\b|\beye (?:pain|infection|irritation|problem)s?\b|"
     r"\b(?:pain|painful|redness|itch\w*|water\w*|swollen|swelling|blurr?\w*|sore|burning|irritation)\b.{0,25}\beyes?\b",
     ("आँख", "आंख", "आंखों", "आँखों", "नज़र", "नजर", "धुंधला", "ಕಣ್ಣು", "ಕಣ್ಣಿನ", "ದೃಷ್ಟಿ")),
    ("Gynecologist",
     r"\bperiods?\b|\bmenstrual\w*|\bmenstruat\w*|\bpregnan\w*|\bvaginal\b|\bwhite discharge\b|\bpcos\b|\bmissed (?:my )?period\b|\bmenopaus\w*",
     ("पीरियड", "मासिक", "माहवारी", "गर्भ", "प्रेग्नेंसी", "ಮುಟ್ಟು", "ಗರ್ಭ", "ಋತುಸ್ರಾವ")),
    ("Psychiatrist",
     r"\banxi\w*|\bdepress\w*|\bstress\w*|\bpanic\b|\bmood\b|\bcan'?t sleep\b|\binsomnia\b|\bsleep (?:problem|trouble|issue)s?\b|"
     r"\bfeeling (?:sad|low|hopeless|down)\b|\bsuicid\w*|\bwant to die\b|\bworr(?:y|ied|ies)\b|\blonely\b|\bgrief\b",
     ("चिंता", "तनाव", "उदास", "डिप्रेशन", "घबराहट", "नींद नहीं", "ಆತಂಕ", "ಒತ್ತಡ", "ಖಿನ್ನತೆ", "ಭಯ", "ನಿದ್ರೆ ಬರುತ್ತಿಲ್ಲ", "ನಿದ್ರೆ ಬರುವುದಿಲ್ಲ")),
)
_COMPILED = tuple((name, re.compile(pattern, re.I), words) for name, pattern, words in _TABLE)
_ORDER = {name: i for i, (name, _, _) in enumerate(_TABLE)}

_CHILD = re.compile(r"\b(?:child|children|kid|kids|baby|babies|infant|toddler|newborn|my son|my daughter)\b", re.I)
_CHILD_OTHER = ("बच्चा", "बच्चे", "बच्ची", "शिशु", "बेटा", "बेटी", "ಮಗು", "ಮಕ್ಕಳ", "ಮಗನ", "ಮಗಳ")

# ---------------------------------------------------------------------------------------------------
# serious symptoms
# ---------------------------------------------------------------------------------------------------
_SERIOUS_OTHER = (
    "सीने में दर्द", "छाती में दर्द", "सांस लेने में तकलीफ", "सांस फूल", "साँस लेने में तकलीफ", "सांस नहीं आ", "बेहोश", "खून बह", "बहुत खून",
    "दौरा पड़", "मिर्गी", "आत्महत्या", "मरना चाहता", "मरना चाहती", "चेहरा टेढ़ा", "बोलने में दिक्कत",
    "ಎದೆ ನೋವು", "ಎದೆನೋವು", "ಉಸಿರಾಟದ ತೊಂದರೆ", "ಉಸಿರಾಡಲು ಕಷ್ಟ", "ಉಸಿರು ಕಟ್ಟ", "ಮೂರ್ಛೆ", "ಪ್ರಜ್ಞೆ ತಪ್ಪ", "ಬಹಳ ರಕ್ತ", "ರಕ್ತಸ್ರಾವ",
    "ಸೆಳವು", "ಫಿಟ್ಸ್", "ಆತ್ಮಹತ್ಯೆ", "ಸಾಯಬೇಕು", "ಮುಖ ಓರೆ",
)
_NEGATION_OTHER = ("नहीं", "नही", "ಇಲ್ಲ", "ಇಲ್ಲವೆ", "ಇಲ್ಲವಲ್ಲ")


_CLAUSE_BREAK = re.compile(r"[.,;!?]|\b(?:and|but|with|also|however|although|though|plus)\b")
_DENIAL = re.compile(
    r"\b(?:no|not|without|never|none|nothing|(?:do|does|did|have|has|had|is|are|was|were)n'?t|"
    r"(?:do|does|did|have|has|had|is|are|was|were) not)\b")


def _denied(lowered: str, start: int) -> bool:
    """Is the symptom at `start` denied ("no fever", "I don't have a cough")? A denial stops at "and", "but" or a
    comma: "my daughter is not eating AND has loose motions" does have loose motions."""
    before = lowered[max(0, start - 60):start]
    cuts = list(_CLAUSE_BREAK.finditer(before))
    clause = before[cuts[-1].end():] if cuts else before
    return bool(_DENIAL.search(clause))


def _other_script_hit(lowered: str, words: tuple[str, ...]) -> bool:
    """A Hindi/Kannada word is in the text and is not followed by a denial ("सीने में दर्द नहीं है")."""
    for word in words:
        start = lowered.find(word)
        while start != -1:
            after = lowered[start + len(word): start + len(word) + 25]
            if not any(neg in after for neg in _NEGATION_OTHER):
                return True
            start = lowered.find(word, start + 1)
    return False


def is_serious(text: str) -> bool:
    """Chest pain, trouble breathing, fainting, heavy bleeding, a seizure, stroke signs or thoughts of self-harm."""
    lowered = _clean(text)
    if live_assist.red_flag_cards(live_assist.statements(text or ""), set()):
        return True
    return _other_script_hit(lowered, _SERIOUS_OTHER)


# ---------------------------------------------------------------------------------------------------
# what the patient said
# ---------------------------------------------------------------------------------------------------

def _clean(text: str) -> str:
    return " ".join((text or "").lower().replace("’", "'").split())


# first-person words: "I have", "my", "me" and the Hindi / Kannada equivalents
_PERSONAL = re.compile(r"\b(?:i|i'm|i've|me|my|mine|myself|having|feeling|suffering|got)\b|\b(?:his|her|our)\b", re.I)
_PERSONAL_OTHER = ("मुझे", "मेरे", "मेरी", "मेरा", "हमें", "ನನಗೆ", "ನನ್ನ", "ನಮಗೆ", "ನಮ್ಮ")
_QUESTION_WORDS = re.compile(r"\b(?:what|where|when|which|who|how|why|timing|timings|ward|visiting|hours|cost|fee|fees|price)\b", re.I)

# anything that sounds like a health complaint (only used to decide whether the model may be asked)
_COMPLAINT = re.compile(
    r"\b(?:pain\w*|ache\w*|hurts?|hurting|sick|unwell|swell\w*|swollen|bleed\w*|vomit\w*|dizzy|weak|infection|itch\w*|burning|"
    r"cramp\w*|sore|allerg\w*|injur\w*|wound|bruise\w*|lump|breath\w*|not feeling)\b", re.I)
_COMPLAINT_OTHER = ("दर्द", "तकलीफ", "बीमार", "जलन", "सूजन", "चोट", "ನೋವು", "ತೊಂದರೆ", "ಉರಿ", "ಊತ", "ಗಾಯ", "ಅನಾರೋಗ್ಯ")


def _is_complaint_like(lowered: str) -> bool:
    return bool(_COMPLAINT.search(lowered)) or any(w in lowered for w in _COMPLAINT_OTHER)


_ASKING = re.compile(r"^(?:is|are|can|could|do|does|did|will|would|should|what|where|when|which|who|how|why)\b|\?\s*$", re.I)


def _is_personal(lowered: str) -> bool:
    """Is the patient talking about a problem of their own (not asking about the hospital)?

    "I have a fever", "my son is coughing" and short complaints without a pronoun ("cough and cold since 4 days")
    all are. A question ("what is a fever", "fever ward timings", "is a headache dangerous?") is not.
    """
    if _PERSONAL.search(lowered) or any(w in lowered for w in _PERSONAL_OTHER):
        return True
    return not _QUESTION_WORDS.search(lowered) and not _ASKING.search(lowered)


def match_symptom(text: str) -> dict | None:
    """{"specialty", "symptom", "serious"} from the symptom table, or None.

    "symptom" is a short plain label for the specialty's area (not a diagnosis). A symptom that is denied ("no
    fever") does not count, and neither does a question about the hospital ("fever ward timings").
    """
    lowered = _clean(text)
    if not lowered or not _is_personal(lowered):
        return None

    scores: dict[str, int] = {}
    for name, pattern, words in _COMPILED:
        hits = sum(1 for m in pattern.finditer(lowered) if not _denied(lowered, m.start()))
        hits += sum(1 for w in words if w in lowered and _other_script_hit(lowered, (w,)))
        if hits:
            scores[name] = hits

    serious = is_serious(text)
    child = bool(_CHILD.search(lowered)) or any(w in lowered for w in _CHILD_OTHER)
    if not scores and not (serious and not child):
        return None
    if child and (scores or _is_complaint_like(lowered) or serious):
        return {"specialty": "Pediatrician", "symptom": "child's health", "serious": serious}
    if not scores:                                                     # serious words only: e.g. "I fainted"
        return {"specialty": "General Physician", "symptom": "general health", "serious": serious}
    best = max(scores, key=lambda n: (scores[n], -_ORDER[n]))
    if best != "Cardiologist" and "Cardiologist" in scores and serious:
        best = "Cardiologist"                                         # chest pain is a heart question first
    return {"specialty": best, "symptom": _label(best), "serious": serious}


_LABELS = {
    "Cardiologist": "heart or chest", "General Physician": "general health", "Neurologist": "headache or dizziness",
    "Orthopedist": "bones or joints", "Dermatologist": "skin or hair", "ENT Specialist": "ear, nose or throat",
    "Ophthalmologist": "eyes", "Gynecologist": "women's health", "Pediatrician": "child's health", "Psychiatrist": "stress or mood",
}


def _label(specialty: str) -> str:
    return _LABELS.get(specialty, "general health")


# The model is only the BACKUP: asked when the message sounds like a health complaint and the table found nothing.
CLASSIFY_PROMPT = (
    "You help a hospital receptionist route a patient to the right kind of doctor. Read the patient's message and "
    "choose ONE specialty from this exact list: " + ", ".join(SPECIALTIES) + ".\n"
    "If the message is not about the patient's own health problem, or you are not sure, answer NONE. "
    "Never diagnose. Reply with ONLY a JSON object: {\"specialty\": \"<one of the list or NONE>\"}"
)


def classify_with_model(text: str, chat=None) -> dict | None:
    """The specialty the model picks from OUR list, or None. Anything not on the list is thrown away."""
    lowered = _clean(text)
    if not lowered or not _is_personal(lowered) or not _is_complaint_like(lowered):
        return None
    if chat is None:
        from services.scribe import summarize
        chat = summarize.call_chat
    try:
        reply = chat([{"role": "system", "content": CLASSIFY_PROMPT}, {"role": "user", "content": text}])
        body = re.sub(r"<think>.*?</think>", "", reply or "", flags=re.S)
        start, end = body.find("{"), body.rfind("}")
        chosen = str(json.loads(body[start:end + 1]).get("specialty") or "").strip()
    except Exception as exc:  # noqa: BLE001 - the backup must never break the chat
        print(f"[SymptomGuide] model could not classify: {exc}")
        return None
    match = next((s for s in SPECIALTIES if s.lower() == chosen.lower()), None)
    if match is None:
        return None
    return {"specialty": match, "symptom": _label(match), "serious": is_serious(text)}


def complaint_fallback(text: str) -> dict | None:
    """A personal health complaint the table and the model could not place: a General Physician is the safe start."""
    lowered = _clean(text)
    if not lowered or not _is_personal(lowered):
        return None
    real = any(not _denied(lowered, m.start()) for m in _COMPLAINT.finditer(lowered)) or _other_script_hit(
        lowered, tuple(w for w in _COMPLAINT_OTHER if w in lowered))
    if not real:
        return None
    return {"specialty": "General Physician", "symptom": _label("General Physician"), "serious": is_serious(text)}


def find_symptom(text: str, chat=None) -> dict | None:
    """The table first; the model only when the table has nothing and the message sounds like a complaint."""
    return match_symptom(text) or classify_with_model(text, chat)


# ---------------------------------------------------------------------------------------------------
# "it did not improve" / "which doctor should I see"
# ---------------------------------------------------------------------------------------------------
_NOT_IMPROVED = re.compile(
    r"\b(?:did ?n[o']?t|does ?n[o']?t|has ?n[o']?t|have ?n[o']?t|not|never|no)\s+(?:\w+\s+){0,2}?(?:improv\w*|better|help\w*|work\w*|go(?:ne)?|relief|subsid\w*|reduc\w*|ease\w*)\b|"
    r"\bno (?:improvement|relief|change|difference)\b|\bstill (?:hurts?|hurting|paining|aching|bothering|troubling|there|the same|bad|severe|persist\w*|have|has|sick|not|in pain)\b|"
    r"\b(?:getting|got|become|became|is|it'?s|it is) (?:worse|worsening|more severe|unbearable)\b|\bworse\b|\bnot improving\b|\bpersist\w*\b", re.I)
_NOT_IMPROVED_OTHER = (
    "ठीक नहीं हुआ", "ठीक नहीं हो रहा", "ठीक नहीं हुई", "आराम नहीं", "फर्क नहीं", "अभी भी", "और बढ़", "बढ़ गया", "बढ़ गई",
    "ಸರಿಹೋಗಿಲ್ಲ", "ಕಡಿಮೆಯಾಗಿಲ್ಲ", "ಗುಣವಾಗಿಲ್ಲ", "ಇನ್ನೂ ನೋವು", "ಇನ್ನೂ ಇದೆ", "ಹೆಚ್ಚಾಗಿದೆ", "ಸುಧಾರಿಸಿಲ್ಲ",
)
_DOCTOR_NOUN = (
    r"(?:doctors?|dr\.?s?|specialists?|physicians?|consultants?|paediatricians?|pediatricians?|cardiologists?|neurologists?|"
    r"dermatologists?|orthop\w*|gynae?cologists?|psychiatrists?|ophthalmologists?|ent specialists?|general physicians?)"
)
# "suggest me THE doctors", "which doctor should I see", "who should I see": the patient wants us to choose.
_WHICH_DOCTOR = re.compile(
    rf"\bwhich (?:kind of |type of |sort of |one of the )?{_DOCTOR_NOUN}\b|\bwho should i (?:see|meet|consult|visit|go to)\b|"
    rf"\bwhat (?:kind|type|sort) of (?:doctor|specialist)\b|\bwho can i (?:see|consult|meet|visit)\b|"
    rf"\b(?:suggest|recommend|advise)\b[^.?!]{{0,30}}\b{_DOCTOR_NOUN}\b|\bright (?:doctor|specialist)\b|\bwhich specialist\b", re.I)
# "show me the doctors", "find a doctor", "doctors available": a request for doctors that may also just be a booking.
_SHOW_DOCTORS = re.compile(
    rf"\b(?:show|list|find|give|tell|get|search|help me find|look(?:ing)? for|need|want)\b[^.?!]{{0,30}}\b{_DOCTOR_NOUN}\b(?!\s+(?:appointment|booking))|"
    rf"\b{_DOCTOR_NOUN}\b[^.?!]{{0,25}}\b(?:available|options|suggestions?|recommendations?|list|names?)\b|"
    rf"\b(?:book|booking)\b[^.?!]{{0,25}}\b(?:a |the |an )?{_DOCTOR_NOUN}\b(?!\s+appointment)", re.I)
_WANT_DOCTOR = re.compile(
    r"\b(?:i )?(?:want|need|would like|wish) (?:to )?(?:see |meet |consult |visit )?(?:a |an |the )?(?:doctor|specialist)\b|"
    r"\bshould i (?:see|consult|meet) (?:a |the )?doctor\b|\bsee a doctor\b", re.I)
_WHICH_OTHER = ("कौन सा डॉक्टर", "कौन से डॉक्टर", "कौन-सा डॉक्टर", "डॉक्टर बताइए", "डॉक्टर सुझाइए", "किस डॉक्टर", "डॉक्टर सुझाओ", "डॉक्टर बताओ",
                "ಯಾವ ವೈದ್ಯರು", "ಯಾವ ಡಾಕ್ಟರ್", "ವೈದ್ಯರನ್ನು ಸೂಚಿಸಿ", "ಡಾಕ್ಟರ್ ಸೂಚಿಸಿ", "ವೈದ್ಯರನ್ನು ತಿಳಿಸಿ")
_WANT_OTHER = ("डॉक्टर चाहिए", "डॉक्टर से मिलना", "डॉक्टर को दिखाना", "डॉक्टर दिखाइए", "डॉक्टरों के नाम", "डॉक्टर की सूची",
               "ವೈದ್ಯರು ಬೇಕು", "ಡಾಕ್ಟರ್ ಬೇಕು", "ವೈದ್ಯರನ್ನು ನೋಡ", "ವೈದ್ಯರನ್ನು ಭೇಟಿ", "ವೈದ್ಯರನ್ನು ತೋರಿಸಿ", "ವೈದ್ಯರ ಹೆಸರು")

# The patient named a kind of doctor or a doctor ("a cardiologist", "Dr. Rao"): that is a normal booking request.
_NAMES_A_DOCTOR = re.compile(
    r"\bdr\.?\s+[a-z]+|\b(?:paediatric\w*|pediatric\w*|cardiolog\w*|neurolog\w*|dermatolog\w*|orthop\w*|gynae?colog\w*|psychiatr\w*|"
    r"ophthalmolog\w*|ent specialist|general physician|dentist|oncolog\w*|urolog\w*)\b", re.I)
_BOOKING_WORD = re.compile(r"\b(?:book|booking|appointment|schedule|reserve)\b", re.I)
_BOOKING_OTHER = ("बुक", "अपॉइंटमेंट", "अपॉइन्टमेंट", "ಬುಕ್", "ಅಪಾಯಿಂಟ್")


def names_a_doctor(text: str, extracted_name: str | None = None) -> bool:
    """Did the patient name a doctor or a kind of doctor?

    `extracted_name` is what the language model read as the doctor's name. It sometimes GUESSES one from the symptom
    ("skin irritation" -> "Dermatologist"), so in English text it only counts when a word of it really is in the
    sentence. (Hindi and Kannada are written in other letters, so there the model's reading is trusted.)
    """
    lowered = _clean(text)
    if _NAMES_A_DOCTOR.search(lowered):
        return True
    if not extracted_name:
        return False
    if re.search(r"[^\x00-\x7f]", lowered):
        return True
    words = {w for w in re.findall(r"[a-z]+", lowered)}
    return any(part in words for part in re.findall(r"[a-z]{4,}", str(extracted_name).lower()))


_DOCTOR_WORD = re.compile(r"\b(?:doctors?|specialists?|department|dept|physicians?)\b", re.I)
_DOCTOR_WORD_OTHER = ("डॉक्टर", "विभाग", "ವೈದ್ಯ", "ಡಾಕ್ಟರ್", "ವಿಭಾಗ")


def mentions_doctor_word(text: str) -> bool:
    """"skin doctor", "ENT department": the patient is choosing a kind of doctor, not just describing a problem."""
    lowered = _clean(text)
    return bool(_DOCTOR_WORD.search(lowered)) or any(w in lowered for w in _DOCTOR_WORD_OTHER)


_SPECIALTY_WORDS = (
    ("Cardiologist", r"\bcardio\w*|\bheart (?:doctor|specialist)\b", ("हृदय रोग", "दिल का डॉक्टर", "ಹೃದ್ರೋಗ", "ಹೃದಯ ತಜ್ಞ")),
    ("Neurologist", r"\bneuro\w*|\bbrain (?:doctor|specialist)\b|\bnerve (?:doctor|specialist)\b", ("न्यूरो", "ನರರೋಗ", "ನ್ಯೂರೋ")),
    ("Orthopedist", r"\bortho\w*|\bbone (?:doctor|specialist)\b", ("हड्डी रोग", "हड्डी के डॉक्टर", "ಮೂಳೆ ತಜ್ಞ", "ಮೂಳೆ ವೈದ್ಯ")),
    ("Dermatologist", r"\bderma\w*|\bskin (?:doctor|specialist)\b", ("त्वचा रोग", "त्वचा के डॉक्टर", "ಚರ್ಮ ರೋಗ", "ಚರ್ಮ ತಜ್ಞ")),
    ("ENT Specialist", r"\bent\b|\bear,? nose(?:,? and| &)? throat\b|\bthroat (?:doctor|specialist)\b", ("कान नाक गला", "ಕಿವಿ ಮೂಗು ಗಂಟಲು")),
    ("Ophthalmologist", r"\bophthal\w*|\bopthal\w*|\beye (?:doctor|specialist)\b", ("नेत्र रोग", "आँख के डॉक्टर", "ಕಣ್ಣಿನ ತಜ್ಞ", "ಕಣ್ಣಿನ ವೈದ್ಯ")),
    ("Gynecologist", r"\bgyn\w*|\bobstetric\w*|\bwomen'?s (?:doctor|specialist)\b", ("स्त्री रोग", "ಸ್ತ್ರೀರೋಗ")),
    ("Pediatrician", r"\bp(?:a)?ediatric\w*|\bchild (?:doctor|specialist)\b|\bkids? doctor\b", ("बाल रोग", "बच्चों के डॉक्टर", "ಮಕ್ಕಳ ತಜ್ಞ", "ಮಕ್ಕಳ ವೈದ್ಯ")),
    ("Psychiatrist", r"\bpsychiatr\w*|\bmental health\b|\bpsycholog\w*", ("मनोचिकित्सक", "ಮನೋವೈದ್ಯ", "ಮಾನಸಿಕ")),
    ("General Physician", r"\bgeneral (?:physician|medicine|doctor)\b|\bfamily doctor\b|\bgp\b", ("सामान्य चिकित्सक", "ಸಾಮಾನ್ಯ ವೈದ್ಯ")),
)
_SPECIALTY_COMPILED = tuple((name, re.compile(pattern, re.I), words) for name, pattern, words in _SPECIALTY_WORDS)


def specialty_in(text: str) -> str | None:
    """The kind of doctor the sentence mentions anywhere in it ("the three orthopedics doctors", "an ENT doctor"), or None."""
    lowered = _clean(text)
    for name, pattern, words in _SPECIALTY_COMPILED:
        if pattern.search(lowered) or any(w in lowered for w in words):
            return name
    return None


def names_a_person(text: str, extracted_name: str | None = None) -> bool:
    """A doctor's own name ("Dr. Rao", or the name the model read and the patient really wrote), not a kind of doctor."""
    lowered = _clean(text)
    if re.search(r"\bdr\.?\s+[a-z]", lowered):
        return True
    if not extracted_name or specialty_in(str(extracted_name)):
        return False
    if re.search(r"[^\x00-\x7f]", lowered):
        return True
    words = set(re.findall(r"[a-z]+", lowered))
    return any(part in words for part in re.findall(r"[a-z]{4,}", str(extracted_name).lower()))


def specialty_named(text: str) -> str | None:
    """A kind of doctor said on its own ("dermatologist", "skin doctor", "ENT", "eye specialist"), or None.

    Only for a short message that is not a question or a booking request: "which type of doctor are you looking
    for?" is answered with "ENT", and that should list ENT doctors.
    """
    lowered = _clean(text)
    if not lowered or len(lowered.split()) > 8 or _ASKING.search(lowered) or _BOOKING_WORD.search(lowered):
        return None
    for name, pattern, words in _SPECIALTY_COMPILED:
        if pattern.search(lowered) or any(w in lowered for w in words):
            return name
    return None


def explicit_booking(text: str) -> bool:
    """The patient used a booking word. (The language model sometimes says "wants to book" for a plain symptom.)"""
    lowered = _clean(text)
    return bool(_BOOKING_WORD.search(lowered)) or any(w in lowered for w in _BOOKING_OTHER)


def doctor_request(text: str) -> str | None:
    """"not_improved" (the tip did not help), "which" (which doctor should I see), "want" (I need a doctor) or None."""
    lowered = _clean(text)
    if not lowered:
        return None
    if _WHICH_DOCTOR.search(lowered) or any(p in lowered for p in _WHICH_OTHER):
        return "which"
    if _NOT_IMPROVED.search(lowered) or any(p in lowered for p in _NOT_IMPROVED_OTHER):
        return "not_improved"
    if _WANT_DOCTOR.search(lowered) or _SHOW_DOCTORS.search(lowered) or any(p in lowered for p in _WANT_OTHER):
        return "want"
    return None


# ---------------------------------------------------------------------------------------------------
# the doctors
# ---------------------------------------------------------------------------------------------------

def _next_free(doctor: dict, get_free_slots, now: datetime) -> str | None:
    """The soonest free slot (ISO time) of this doctor over the next few days, or None."""
    for offset in range(DAYS_AHEAD):
        day = (now.astimezone(IST).date() + timedelta(days=offset)).isoformat()
        try:
            slots = get_free_slots(doctor["doctor_id"], day)
        except Exception as exc:  # noqa: BLE001 - one doctor's lookup failing must not hide the others
            print(f"[SymptomGuide] slots for {doctor.get('doctor_id')} failed: {exc}")
            return None
        upcoming = sorted(
            (s["slot_start"] for s in slots if datetime.fromisoformat(s["slot_start"]) > now),
            key=lambda iso: datetime.fromisoformat(iso))
        if upcoming:
            return upcoming[0]
    return None


def suggest(specialty: str, city: str | None, find_doctors, get_free_slots, now: datetime | None = None) -> list[dict]:
    """Up to three doctors of this specialty, the one with the earliest free time first.

    Doctors in the patient's city come first. If that city has none, every city is searched. Each result is the
    doctor's own record plus "next_slot" (an ISO time, or None when nothing is free in the next few days), and
    "any_city" is True when the city had none. Doctors with no free time come last.
    """
    now = now or datetime.now(IST)
    in_city = [d for d in (find_doctors(specialty, city) if city else []) if _same_specialty(d, specialty)]
    doctors, any_city = in_city, False
    if not doctors:
        doctors = [d for d in find_doctors(specialty, None) if _same_specialty(d, specialty)]
        any_city = bool(city)
    doctors = doctors[:MAX_CANDIDATES]
    if not doctors:
        return []

    with ThreadPoolExecutor(max_workers=min(8, len(doctors))) as pool:
        times = list(pool.map(lambda d: _next_free(d, get_free_slots, now), doctors))
    ranked = sorted(zip(doctors, times), key=lambda pair: (pair[1] is None, pair[1] or ""))
    return [{**doctor, "next_slot": slot, "any_city": any_city} for doctor, slot in ranked[:MAX_SUGGESTIONS]]


def _same_specialty(doctor: dict, specialty: str) -> bool:
    return str(doctor.get("specialty") or "").strip().lower() == specialty.lower()
