# WhatsApp templates for reminders and notices

Reminders run in **mock mode** by default (`REMINDER_MODE=mock`): the message is
written to the `reminders` table and the log, and nothing is sent. To send for
real, register the five templates below with Meta, wait for approval, then set
`REMINDER_MODE=live`. Until then nothing here reaches a phone.

WhatsApp only lets a business start a conversation with an **approved template**.
Parameters are **named** (`{{name}}`), they are sent in this order, and the
template language must match `META_WHATSAPP_TEMPLATE_LANGUAGE` (default `en`).
Category: **Utility**. Do not put promotional text in them.

| Template (default name) | Setting that renames it | Parameters (in order) |
|---|---|---|
| `zenvy_appointment_reminder` | `META_WHATSAPP_REMINDER_TEMPLATE_NAME` | `name`, `doctor`, `date`, `time`, `location`, `when` |
| `zenvy_followup_booked` | `META_WHATSAPP_FOLLOWUP_TEMPLATE_NAME` | `name`, `doctor`, `date`, `time`, `location` |
| `zenvy_appointment_cancelled` | `META_WHATSAPP_CANCELLED_TEMPLATE_NAME` | `name`, `doctor`, `date`, `time` |
| `zenvy_appointment_rescheduled` | `META_WHATSAPP_RESCHEDULED_TEMPLATE_NAME` | `name`, `doctor`, `date`, `time`, `location` |
| `zenvy_doctor_notice` | `META_WHATSAPP_DOCTOR_NOTICE_TEMPLATE_NAME` | `message` |

The booking confirmation and welcome messages that already existed keep their own
templates (`zenvy_appointment_confirmation`, `zenvy_welcome`) and are unchanged.

## Suggested wording (English)

**zenvy_appointment_reminder** - used for both the 24-hour and the 2-hour reminder;
`{{when}}` is "tomorrow" or "in 2 hours".

> Hello {{name}}, a reminder: your appointment with {{doctor}} is {{when}} - {{date}} at {{time}}, {{location}}.

**zenvy_followup_booked**

> Hello {{name}}, your follow-up visit with {{doctor}} is booked for {{date}} at {{time}}, {{location}}.

**zenvy_appointment_cancelled**

> Hello {{name}}, your appointment with {{doctor}} on {{date}} at {{time}} has been cancelled.

**zenvy_appointment_rescheduled**

> Hello {{name}}, your appointment with {{doctor}} has been moved to {{date}} at {{time}}, {{location}}.

**zenvy_doctor_notice** - the doctor's messages ("New appointment", "Cancelled",
"Rescheduled", reminders) are short sentences built by the server and sent as one
parameter:

> Zenvy: {{message}}

Example of `{{message}}`: `New appointment: Patient ••••1234 on Mon 12 Oct at 9:00 AM.`
The doctor sees only the **last four digits** of the patient's phone number.

## Sample values Meta asks for

`name` = Asha, `doctor` = Dr. Arjun Rao, `date` = Mon 12 Oct, `time` = 9:00 AM,
`location` = Zenvy Care Hospital, `when` = tomorrow,
`message` = New appointment: Patient ••••1234 on Mon 12 Oct at 9:00 AM.

## Notes

* A doctor can only be messaged if the doctor's login phone number is a real
  WhatsApp number. The seeded doctors have placeholder numbers (`+9100000000NN`)
  and will fail in live mode (recorded as `failed`, one retry after
  `REMINDER_RETRY_MINUTES`).
* Hindi and Kannada versions are not registered. Everything is sent in English
  until translated templates exist.
* Nothing in the test suite can contact WhatsApp: `tests/conftest.py` removes the
  credentials and makes the WhatsApp sender raise instead of calling Meta.
