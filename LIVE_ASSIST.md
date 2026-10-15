# Live Assist: AI suggestions for the doctor during a consultation

While the doctor records a consultation on the `/doctor` page, **Live Assist** listens to the same
microphone and shows short suggestion cards, as text, until the recording is stopped. Only the doctor
sees them. Nothing is written to the note, the prescription or the patient's record.

## What the doctor sees

| Card | Meaning |
|---|---|
| **ASK NEXT** | A question that looks missing (allergies, duration, associated symptoms...) |
| **RED FLAG** | Something said that may need urgent attention |
| **HISTORY** | A fact from this doctor's own earlier visits with the patient |
| **WRAP-UP** | Something not covered yet (how long to continue, when to come back) |
| **KEEP IN MIND** | A gentle thing to consider. Never a diagnosis. |

Every card shows the words it is based on ("because: ..."). Actions: **Pin**, **Add to plan**,
**Ask this** (marks a question as asked) and **Dismiss**. **Pause Live Assist** stops listening for a
moment (the recording continues). "Add to plan" collects ready-to-copy text next to the note editor;
nothing is written into the note for the doctor. A collapsible **Live transcript** shows what was heard.

## How it works

```
browser microphone --(6 s pieces, cut at pauses, 16 kHz WAV)--> gateway /doctor/api/consultations/{id}/live/audio
   -> speech-to-text -> rolling transcript (memory only)
   -> built-in red-flag rules (every piece, immediately)
   -> language model (at most every 12 s, when 8+ new words) -> checks -> cards stored in Team C
browser polls GET .../live every 3 s -> panel
```

* The full recording is still captured and processed after the visit exactly as before.
* The live pieces are processed and discarded; only the final recording is stored (encrypted).
* Delay: a card appears roughly 10-20 seconds after the sentence.

## Safety rules (enforced by code, not just by the prompt)

1. **Grounded:** a card's quoted evidence must really occur in the conversation (or the earlier-visit
   notes for a history card), or it is dropped.
2. **Never** a medicine name or dose, a test order, a disease name or a stated diagnosis. Such cards are dropped.
3. **No repeats** of anything already shown.
4. **Built-in red-flag rules** (chest pain, difficulty breathing, fainting, seizure, heavy bleeding,
   self-harm, stroke signs, sudden severe headache, swelling of face/throat) fire even if the model says
   nothing. They look at statements, not at the doctor's questions ("Any chest pain?"), and ignore
   denials ("no chest pain"). They are English wording; the model covers other languages.
5. A model card labelled "red flag" without urgent wording is downgraded to "ask"/"keep in mind".
6. **Consent first:** nothing is processed or stored unless the patient's recording consent is "granted".
   Withdrawing consent deletes the cards.
7. **Failure is quiet:** if speech or the model is unavailable the panel says "Live Assist paused" and
   the recording and the final note are unaffected.

## Storage and retention

Table `live_suggestions` (migration `014_live_suggestions.sql`): the card, its evidence, and what the
doctor did with it. It holds no phone numbers and is not part of the medical note. It is deleted with the
transcript: when consent is withdrawn, when the recording is deleted, and by the daily retention purge
(`TRANSCRIPT_RETENTION_DAYS`, default 90).

## Settings

| Setting | Default | Meaning |
|---|---|---|
| `LIVE_ASSIST_ENABLED` (gateway) | `true` | `false` hides the panel and rejects live audio |

Tuning constants are in `services/scribe/live.py`: `CHUNK_SECONDS` (6), `ENGINE_MIN_INTERVAL_SECONDS`
(12), `MIN_NEW_WORDS` (8), `MAX_CARDS_VISIBLE` (5).

## Limits

* English suggestions only. Speaker labels are guessed from pauses and can be wrong.
* One microphone: in-person consultations. Phone and online-call capture are not covered.
* No medicine, dose or test suggestions in this phase.
* The conversation is held in the gateway's memory for the visit (one gateway instance).
* Each live update costs a speech call and a model call; the Render free plan is too small for this.

## Files

* `services/agents/live_assist.py` - the prompt, parsing, safety filters, grounding and red-flag rules
* `services/scribe/live.py` - live sessions: transcript, speaker guess, when the engine runs
* `services/gateway/doctor_routes.py` - the `/live/*` endpoints
* `services/gateway/static/doctor.html` - capture and the panel
* `DB-Team-C/app/services/live_service.py`, `app/api/routes/live.py` - storage and routes
