-- Migration 008: hospitals, doctors, weekly schedules and bookable slots.
--
-- doctor_slots is what makes double-booking impossible: each (doctor, start)
-- exists once, and the application claims a slot with a single conditional
-- UPDATE (available -> held -> booked). See app/services/slot_service.py.

CREATE TABLE IF NOT EXISTS hospitals (
    hospital_id VARCHAR PRIMARY KEY,
    name VARCHAR NOT NULL,
    city VARCHAR NOT NULL,
    latitude DOUBLE PRECISION,
    longitude DOUBLE PRECISION,
    timings VARCHAR,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_hospitals_city ON hospitals(city);

CREATE TABLE IF NOT EXISTS doctors (
    doctor_id VARCHAR PRIMARY KEY,
    hospital_id VARCHAR NOT NULL REFERENCES hospitals(hospital_id),
    auth_id VARCHAR UNIQUE REFERENCES authentication(auth_id),
    name VARCHAR NOT NULL,
    specialty VARCHAR NOT NULL,
    slot_minutes INTEGER NOT NULL DEFAULT 30 CHECK (slot_minutes > 0),
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_doctors_hospital_id ON doctors(hospital_id);
CREATE INDEX IF NOT EXISTS idx_doctors_name ON doctors(name);
CREATE INDEX IF NOT EXISTS idx_doctors_specialty ON doctors(specialty);

CREATE TABLE IF NOT EXISTS doctor_schedules (
    schedule_id VARCHAR PRIMARY KEY,
    doctor_id VARCHAR NOT NULL REFERENCES doctors(doctor_id) ON DELETE CASCADE,
    weekday INTEGER NOT NULL CHECK (weekday BETWEEN 0 AND 6),
    start_time TIME NOT NULL,
    end_time TIME NOT NULL,
    CONSTRAINT uq_doctor_schedules_block UNIQUE (doctor_id, weekday, start_time),
    CHECK (end_time > start_time)
);

CREATE INDEX IF NOT EXISTS idx_doctor_schedules_doctor_id ON doctor_schedules(doctor_id);

CREATE TABLE IF NOT EXISTS doctor_slots (
    slot_id VARCHAR PRIMARY KEY,
    doctor_id VARCHAR NOT NULL REFERENCES doctors(doctor_id),
    slot_start TIMESTAMPTZ NOT NULL,
    slot_end TIMESTAMPTZ NOT NULL,
    status VARCHAR NOT NULL DEFAULT 'available'
        CHECK (status IN ('available', 'held', 'booked')),
    held_by_session VARCHAR,
    held_until TIMESTAMPTZ,
    appointment_id VARCHAR,
    CONSTRAINT uq_doctor_slots_doctor_start UNIQUE (doctor_id, slot_start),
    CHECK (slot_end > slot_start)
);

CREATE INDEX IF NOT EXISTS idx_doctor_slots_doctor_id ON doctor_slots(doctor_id);
CREATE INDEX IF NOT EXISTS idx_doctor_slots_slot_start ON doctor_slots(slot_start);
CREATE INDEX IF NOT EXISTS idx_doctor_slots_status ON doctor_slots(status);
