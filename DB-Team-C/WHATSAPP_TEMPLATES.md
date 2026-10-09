# WhatsApp templates for reminders and notices

`REMINDER_MODE` (in `DB-Team-C/.env`) decides whether reminders are really sent:

* `live` - sent through Meta using the templates below.
* `mock` - written to the `reminders` table and the log only; nothing is sent.

WhatsApp only lets a business start a conversation with an **approved template**.
Parameters are **named** and must match the template exactly or Meta rejects the
send. Language is `en`, category **Utility**.

Live mode only sends to real Indian mobile numbers (10 digits starting 6-9, with or
without +91). The seeded doctor logins (`+9100000000NN`) and test numbers are
skipped without calling Meta (`skipped`, "not a real mobile number").

## Which template sends what

| Message | Template | Status in Meta | Parameters |
|---|---|---|---|
| Patient: cancelled | `appointment_cancelled` | **Approved** (already existed) | `doctor_name`, `patient_name`, `appointment_date`, `appointment_time` |
| Patient: rescheduled | `appointment_rescheduled` | **Approved** (already existed) | `doctor_name`, `patient_name`, `new_date`, `new_time` |
| Patient: booking confirmation | `zenvy_appointment_confirmation` | **Approved** (unchanged) | `name`, `doctor`, `date`, `time`, `location`, `id` |
| Patient: 24 h and 2 h reminder | `zenvy_appointment_reminder` | Submitted, pending review | `name`, `doctor_name`, `when`, `date`, `time`, `location` |
| Patient: follow-up booked | `zenvy_followup_booked` | Submitted, pending review | `name`, `doctor_name`, `date`, `time`, `location` |
| Doctor: every notice and reminder | `zenvy_doctor_notice` | Submitted, pending review | `event`, `patient`, `date`, `time` |

`doctor_name` is sent **without** "Dr." because the templates already say "Dr.".
Template names can be changed with the `META_WHATSAPP_*_TEMPLATE_NAME` settings.

Until a pending template is approved, a send fails; it is retried once after
`REMINDER_RETRY_MINUTES` and then recorded as `failed` (see the appointment's History
on the doctor page). Check approval in Meta WhatsApp Manager, or list them with the
`GET /{WABA_ID}/message_templates` API.

## Wording of the templates submitted by this project

**zenvy_appointment_reminder** - used for both the 24-hour and the 2-hour reminder
(`{{when}}` is "tomorrow" or "in 2 hours"):

> Hello {{name}}, this is a reminder that your appointment with Dr. {{doctor_name}} is {{when}}, on {{date}} at {{time}}, at {{location}}. Please arrive 10 minutes early.

**zenvy_followup_booked**

> Hello {{name}}, your follow-up visit with Dr. {{doctor_name}} has been booked for {{date}} at {{time}}, at {{location}}. Please contact the clinic if this time does not suit you.

**zenvy_doctor_notice** - `{{event}}` is e.g. "New appointment", "Cancelled",
"Rescheduled from Mon 12 Oct at 9:00 AM", "Reminder: appointment tomorrow":

> Zenvy appointment update: {{event}}. Patient: {{patient}}. Date: {{date}}. Time: {{time}}. Please check your Zenvy dashboard for details.

The doctor sees only the **last four digits** of the patient's phone number.

## Existing approved templates this project reuses

> **appointment_cancelled:** Appointment update for your booking with Dr. {{doctor_name}}. {{patient_name}}, your appointment on {{appointment_date}} at {{appointment_time}} has been cancelled. Please contact the clinic to book a new slot.

> **appointment_rescheduled:** Appointment update for your booking with Dr. {{doctor_name}}. {{patient_name}}, your appointment has been rescheduled to {{new_date}} at {{new_time}}. Please contact the clinic if this time does not suit you.

## Notes

* A doctor can only be messaged if the doctor's login phone number is a real
  WhatsApp number.
* Hindi and Kannada versions are not registered. Everything is sent in English.
* Nothing in the test suite can contact WhatsApp: `tests/conftest.py` removes the
  credentials and makes the WhatsApp sender raise instead of calling Meta.
