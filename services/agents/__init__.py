"""The agents of the Zenvy pilot.

* patient_agent  - talks to the patient (chat / voice): medicines, next dose, "I took it".
* doctor_agent   - talks to the doctor (Assistant panel): schedule, pending approvals, updates,
                   history summary, drafts. It never approves or signs anything.
* the coordinator lives in Team C (app/services/agent_service.py) because it works on the database.

Language models understand and draft; plain code does the actual work after rule checks.
"""
