import sys
import csv
from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

from services.orchestrator.entity_extraction import extract_booking_fields
from services.llm.client import generate_reply

app = FastAPI(title="Zenvy Team B NLU Tester")

RESULTS_FILE = Path(__file__).parent / "test_results.csv"


def save_test_result(sentence, result, llm_response):
    file_exists = RESULTS_FILE.exists() and RESULTS_FILE.stat().st_size > 0

    with open(RESULTS_FILE, "a", newline="", encoding="utf-8") as file:
        writer = csv.writer(file)

        if not file_exists:
            writer.writerow([
                "Test Number",
                "Sentence",
                "Booking Intent",
                "Doctor/Department",
                "Appointment Date",
                "Appointment Time",
                "Confirmation",
                "Is Emergency",
                "Emergency Symptoms",
                "Urgency",
                "LLM Response"
            ])

        with open(RESULTS_FILE, "r", encoding="utf-8") as existing_file:
            test_number = sum(1 for _ in existing_file)

        writer.writerow([
            test_number,
            sentence,
            result.get("wants_to_book"),
            result.get("doctor_name"),
            result.get("appointment_date"),
            result.get("appointment_time"),
            result.get("confirms_booking"),
            result.get("is_emergency"),
            result.get("emergency_symptoms"),
            result.get("urgency"),
            llm_response
        ])


templates = Jinja2Templates(
    directory=str(Path(__file__).parent / "templates")
)


@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "result": None,
            "sentence": "",
            "error": None,
            "llm_response": None,
        },
    )


@app.post("/", response_class=HTMLResponse)
def analyze(request: Request, sentence: str = Form(...)):
    try:
        result = extract_booking_fields(sentence)

        # Generate the actual AI response in English
        llm_response = generate_reply(sentence, "en")

        save_test_result(sentence, result, llm_response)

        error = None

    except Exception as e:
        result = None
        llm_response = None
        error = str(e)

    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "result": result,
            "sentence": sentence,
            "error": error,
            "llm_response": llm_response,
        },
    )