from datetime import date, timedelta

from services.orchestrator.entity_extraction import extract_booking_fields


def test_basic_booking_intent():
    result = extract_booking_fields("I want to book an appointment")
    assert result["wants_to_book"] is True


def test_doctor_department():
    result = extract_booking_fields("I need to see a bone doctor")
    assert result["wants_to_book"] is True
    assert result["doctor_name"] == "Orthopaedics"


def test_cardiology():
    result = extract_booking_fields("I want to see a heart doctor")
    assert result["wants_to_book"] is True
    assert result["doctor_name"] == "Cardiology"


def test_date_and_time():
    result = extract_booking_fields("I want an appointment tomorrow at 3 PM")
    assert result["wants_to_book"] is True
    assert result["appointment_date"] == (
        date.today() + timedelta(days=1)
    ).isoformat()
    assert result["appointment_time"] == "15:00"


def test_confirmation():
    result = extract_booking_fields("yes please")
    assert result["confirms_booking"] is True


def test_cancellation():
    result = extract_booking_fields("cancel it")
    assert result["confirms_booking"] is False
