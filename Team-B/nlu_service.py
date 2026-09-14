from fastapi import FastAPI
from services.orchestrator.entity_extraction import extract_booking_fields

app = FastAPI(title="Zenvy NLU Service")


@app.post("/understand")
def understand(payload: dict):
    text = payload.get("text", "")

    if not text:
        return {
            "intent": "Unclear",
            "entities": {},
            "language": "en"
        }

    result = extract_booking_fields(text)

    return {
        "intent": result.get("intent"),
        "entities": {
            "doctor_name": result.get("doctor_name"),
            "appointment_date": result.get("appointment_date"),
            "appointment_time": result.get("appointment_time"),
            "uhid": result.get("uhid"),
        },
        "language": result.get("language")
    }