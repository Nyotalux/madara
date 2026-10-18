"""Consultations et ordonnances : règle métier, web et API (jalon 4)."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

import pytest
from django.core.exceptions import ValidationError
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import Membership
from apps.appointments import services as appointment_services
from apps.appointments.models import Appointment
from apps.common.context import clinic_context
from apps.consultations import services
from apps.consultations.models import Consultation, Prescription
from apps.patients.models import Patient

from .factories import UserFactory, make_staff

pytestmark = pytest.mark.django_db

STATUS = Appointment.Status


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_patient(clinic, **kwargs):
    defaults = {
        "clinic": clinic,
        "first_name": "Yasmine",
        "last_name": "Idrissi",
        "phone": "+212612345678",
    }
    defaults.update(kwargs)
    return Patient.objects.create(**defaults)


def next_open_day(*, offset=1) -> date:
    day = timezone.localdate() + timedelta(days=offset)
    while day.weekday() in (5, 6):
        day += timedelta(days=1)
    return day


def at(day: date, hour: int, minute: int = 0):
    return timezone.make_aware(datetime.combine(day, time(hour, minute)))


@pytest.fixture
def team(clinic, other_clinic):
    doctor, doctor_membership = make_staff(
        clinic, Membership.Role.DOCTOR, first_name="Salma", last_name="Bennis"
    )
    colleague, colleague_membership = make_staff(
        clinic, Membership.Role.DOCTOR, first_name="Karim", last_name="Naciri"
    )
    nurse, nurse_membership = make_staff(
        clinic, Membership.Role.NURSE, first_name="Sofia", last_name="Kettani"
    )
    reception, reception_membership = make_staff(
        clinic, Membership.Role.RECEPTION, first_name="Nadia", last_name="Amrani"
    )
    rival, rival_membership = make_staff(
        other_clinic, Membership.Role.DOCTOR, first_name="Rachid", last_name="Idrissi"
    )
    return {
        "clinic": clinic,
        "other_clinic": other_clinic,
        "doctor": doctor,
        "doctor_membership": doctor_membership,
        "colleague": colleague,
        "colleague_membership": colleague_membership,
        "nurse": nurse,
        "nurse_membership": nurse_membership,
        "reception": reception,
        "reception_membership": reception_membership,
        "rival": rival,
        "rival_membership": rival_membership,
    }


@pytest.fixture
def patient(clinic):
    return make_patient(clinic)


def book(clinic, patient, membership, day, hour=9, minute=0, **kwargs):
    with clinic_context(clinic):
        return appointment_services.book_appointment(
            clinic=clinic,
            patient=patient,
            practitioner=membership,
            start_at=at(day, hour, minute),
            **kwargs,
        )


def make_consultation(clinic, patient, membership, *, status=None, **kwargs):
    with clinic_context(clinic):
        consultation = services.start_consultation(
            clinic=clinic,
            patient=patient,
            practitioner=membership,
            **kwargs,
        )
    if status == Consultation.Status.COMPLETED:
        services.complete_consultation(consultation)
    return consultation


# ---------------------------------------------------------------------------
# Règles métier
# ---------------------------------------------------------------------------


def test_start_consultation_assigns_reference_and_moves_appointment(team, patient):
    day = next_open_day()
    appointment = book(
        team["clinic"],
        patient,
        team["doctor_membership"],
        day,
        9,
        status=STATUS.ARRIVED,
    )
    with clinic_context(team["clinic"]):
        consultation = services.start_consultation(
            clinic=team["clinic"],
            patient=patient,
            practitioner=team["doctor_membership"],
            appointment=appointment,
            user=team["doctor"],
        )
    assert consultation.reference.startswith("CONS-")
    assert consultation.status == Consultation.Status.IN_PROGRESS
    assert consultation.patient_id == patient.pk
    assert consultation.practitioner_id == team["doctor_membership"].pk
    appointment.refresh_from_db()
    assert appointment.status == STATUS.IN_PROGRESS


def test_start_consultation_is_idempotent_for_same_appointment(team, patient):
    day = next_open_day()
    appointment = book(
        team["clinic"], patient, team["doctor_membership"], day, 9, status=STATUS.ARRIVED
    )
    first = make_consultation(
        team["clinic"], patient, team["doctor_membership"], appointment=appointment
    )
    second = make_consultation(
        team["clinic"], patient, team["doctor_membership"], appointment=appointment
    )
    assert first.pk == second.pk
    assert Consultation.all_objects.filter(appointment=appointment).count() == 1


def test_complete_consultation_closes_appointment(team, patient):
    day = next_open_day()
    appointment = book(
        team["clinic"], patient, team["doctor_membership"], day, 9, status=STATUS.ARRIVED
    )
    consultation = make_consultation(
        team["clinic"], patient, team["doctor_membership"], appointment=appointment
    )
    services.complete_consultation(consultation, user=team["doctor"])
    consultation.refresh_from_db()
    appointment.refresh_from_db()
    assert consultation.status == Consultation.Status.COMPLETED
    assert consultation.ended_at is not None
    assert appointment.status == STATUS.DONE


def test_completed_consultation_cannot_be_cancelled(team, patient):
    consultation = make_consultation(
        team["clinic"],
        patient,
        team["doctor_membership"],
        status=Consultation.Status.COMPLETED,
    )
    with pytest.raises(ValidationError):
        services.cancel_consultation(consultation, reason="Trop tard")


def test_cancel_consultation_keeps_trace(team, patient):
    consultation = make_consultation(team["clinic"], patient, team["doctor_membership"])
    services.cancel_consultation(consultation, reason="Patient parti")
    consultation.refresh_from_db()
    assert consultation.status == Consultation.Status.CANCELLED
    assert "Patient parti" in consultation.notes


def test_add_prescription_creates_items(team, patient):
    consultation = make_consultation(team["clinic"], patient, team["doctor_membership"])
    prescription = services.add_prescription(
        consultation,
        items=[
            {"medication": "Paracétamol 1 g", "frequency": "3 fois par jour"},
            {"medication": "", "dosage": "ignoré"},
        ],
        user=team["doctor"],
    )
    assert prescription.reference.startswith("ORD-")
    assert prescription.patient_id == patient.pk
    assert prescription.practitioner_id == team["doctor_membership"].pk
    assert prescription.items.count() == 1


def test_add_prescription_refused_on_cancelled(team, patient):
    consultation = make_consultation(team["clinic"], patient, team["doctor_membership"])
    services.cancel_consultation(consultation, reason="Annulé")
    with pytest.raises(ValidationError):
        services.add_prescription(consultation, items=[{"medication": "X"}])


def test_vital_signs_helpers(team, patient):
    consultation = make_consultation(team["clinic"], patient, team["doctor_membership"])
    consultation.systolic = 120
    consultation.diastolic = 80
    consultation.weight_kg = 70
    consultation.height_cm = 175
    assert consultation.blood_pressure_display == "120/80 mmHg"
    assert consultation.bmi == 22.9
    assert consultation.has_vital_signs


# ---------------------------------------------------------------------------
# Web
# ---------------------------------------------------------------------------


def test_consultation_pages_render(client, team, patient):
    consultation = make_consultation(team["clinic"], patient, team["doctor_membership"])
    client.force_login(team["doctor"])
    assert client.get(reverse("web:consultation-list")).status_code == 200
    detail = client.get(reverse("web:consultation-detail", args=[consultation.pk]))
    assert detail.status_code == 200
    assert consultation.reference.encode() in detail.content
    assert client.get(reverse("web:consultation-create")).status_code == 200


def test_start_consultation_from_waiting_room(client, team, patient):
    day = next_open_day()
    appointment = book(
        team["clinic"], patient, team["doctor_membership"], day, 9, status=STATUS.ARRIVED
    )
    client.force_login(team["doctor"])
    page = client.get(reverse("web:consultation-start", args=[appointment.pk]))
    assert page.status_code == 200

    response = client.post(
        reverse("web:consultation-start", args=[appointment.pk]),
        {"kind": Consultation.Kind.FIRST, "chief_complaint": "Fièvre"},
    )
    assert response.status_code == 302
    consultation = Consultation.objects.get(appointment=appointment)
    assert consultation.chief_complaint == "Fièvre"
    appointment.refresh_from_db()
    assert appointment.status == STATUS.IN_PROGRESS


def test_reception_cannot_start_consultation(client, team, patient):
    day = next_open_day()
    appointment = book(
        team["clinic"], patient, team["doctor_membership"], day, 9, status=STATUS.ARRIVED
    )
    client.force_login(team["reception"])
    response = client.get(reverse("web:consultation-start", args=[appointment.pk]))
    assert response.status_code == 403


def test_doctor_does_not_see_colleague_consultation(client, team, patient):
    mine = make_consultation(team["clinic"], patient, team["doctor_membership"])
    other_patient = make_patient(team["clinic"], first_name="Rim", last_name="Sabri")
    colleague = make_consultation(
        team["clinic"], other_patient, team["colleague_membership"]
    )
    client.force_login(team["doctor"])
    content = client.get(reverse("web:consultation-list")).content
    assert mine.reference.encode() in content
    assert colleague.reference.encode() not in content
    assert (
        client.get(reverse("web:consultation-detail", args=[colleague.pk])).status_code
        == 404
    )


def test_complete_consultation_via_web(client, team, patient):
    day = next_open_day()
    appointment = book(
        team["clinic"], patient, team["doctor_membership"], day, 9, status=STATUS.ARRIVED
    )
    consultation = make_consultation(
        team["clinic"], patient, team["doctor_membership"], appointment=appointment
    )
    client.force_login(team["doctor"])
    response = client.post(reverse("web:consultation-complete", args=[consultation.pk]))
    assert response.status_code == 302
    consultation.refresh_from_db()
    appointment.refresh_from_db()
    assert consultation.status == Consultation.Status.COMPLETED
    assert appointment.status == STATUS.DONE


def test_prescription_create_via_web(client, team, patient):
    consultation = make_consultation(team["clinic"], patient, team["doctor_membership"])
    client.force_login(team["doctor"])
    response = client.post(
        reverse("web:prescription-create", args=[consultation.pk]),
        {
            "issued_at": timezone.localdate().isoformat(),
            "notes": "",
            "items-TOTAL_FORMS": "1",
            "items-INITIAL_FORMS": "0",
            "items-MIN_NUM_FORMS": "0",
            "items-MAX_NUM_FORMS": "1000",
            "items-0-medication": "Amoxicilline 500 mg",
            "items-0-dosage": "1 gélule",
            "items-0-frequency": "2 fois par jour",
            "items-0-duration": "7 jours",
            "items-0-instructions": "Terminer la cure",
        },
    )
    assert response.status_code == 302
    prescription = Prescription.objects.get(consultation=consultation)
    assert prescription.items.count() == 1


def test_prescription_print_renders(client, team, patient):
    consultation = make_consultation(team["clinic"], patient, team["doctor_membership"])
    prescription = services.add_prescription(
        consultation, items=[{"medication": "Paracétamol 1 g"}]
    )
    client.force_login(team["reception"])
    response = client.get(reverse("web:prescription-print", args=[prescription.pk]))
    assert response.status_code == 200
    assert b"Parac" in response.content


def test_consultation_from_other_clinic_is_not_reachable(client, team, patient):
    consultation = make_consultation(team["clinic"], patient, team["doctor_membership"])
    client.force_login(team["rival"])
    assert (
        client.get(reverse("web:consultation-detail", args=[consultation.pk])).status_code
        == 404
    )


def test_admin_consultation_pages_render(client, team, patient):
    consultation = make_consultation(team["clinic"], patient, team["doctor_membership"])
    platform_admin = UserFactory.create(
        email="superadmin@example.ma",
        is_staff=True,
        is_superuser=True,
        is_platform_staff=True,
    )
    client.force_login(platform_admin)
    assert client.get("/admin/consultations/consultation/").status_code == 200
    assert (
        client.get(
            f"/admin/consultations/consultation/{consultation.pk}/change/"
        ).status_code
        == 200
    )
    assert client.get("/admin/consultations/prescription/").status_code == 200


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------


def test_api_lists_only_current_clinic(api_doctor, team, patient):
    mine = make_consultation(team["clinic"], patient, team["doctor_membership"])
    other_patient = make_patient(
        team["other_clinic"], first_name="Rim", last_name="Sabri"
    )
    make_consultation(team["other_clinic"], other_patient, team["rival_membership"])
    response = api_doctor.get("/api/v1/consultations/")
    assert response.status_code == 200
    payload = response.json()["results"]
    assert [row["reference"] for row in payload] == [mine.reference]
    assert payload[0]["patient_name"] == patient.display_name


@pytest.fixture
def api_doctor(team):
    client = APIClient()
    client.force_authenticate(user=team["doctor"])
    return client


def test_api_create_consultation_from_appointment(team, patient):
    day = next_open_day()
    appointment = book(
        team["clinic"], patient, team["doctor_membership"], day, 9, status=STATUS.ARRIVED
    )
    api = APIClient()
    api.force_authenticate(user=team["doctor"])
    response = api.post(
        "/api/v1/consultations/",
        {"appointment": appointment.pk, "chief_complaint": "Céphalées"},
        format="json",
    )
    assert response.status_code == 201, response.json()
    payload = response.json()
    assert payload["reference"].startswith("CONS-")
    assert payload["patient"] == patient.pk
    appointment.refresh_from_db()
    assert appointment.status == STATUS.IN_PROGRESS


def test_api_complete_action(team, patient):
    consultation = make_consultation(team["clinic"], patient, team["doctor_membership"])
    api = APIClient()
    api.force_authenticate(user=team["doctor"])
    response = api.post(f"/api/v1/consultations/{consultation.pk}/complete/")
    assert response.status_code == 200
    assert response.json()["status"] == Consultation.Status.COMPLETED


def test_api_reception_cannot_create_consultation(team, patient):
    api = APIClient()
    api.force_authenticate(user=team["reception"])
    response = api.post(
        "/api/v1/consultations/",
        {"patient": patient.pk, "practitioner": team["doctor_membership"].pk},
        format="json",
    )
    assert response.status_code == 403


def test_api_prescription_create_and_list(team, patient):
    consultation = make_consultation(team["clinic"], patient, team["doctor_membership"])
    api = APIClient()
    api.force_authenticate(user=team["doctor"])
    response = api.post(
        "/api/v1/prescriptions/",
        {
            "consultation": consultation.pk,
            "items": [{"medication": "Ibuprofène 400 mg", "frequency": "2/j"}],
        },
        format="json",
    )
    assert response.status_code == 201, response.json()
    assert response.json()["item_count"] == 1

    listed = api.get("/api/v1/prescriptions/", {"consultation": consultation.pk})
    assert listed.status_code == 200
    assert len(listed.json()["results"]) == 1


def test_api_refuses_accountant(team):
    accountant, _ = make_staff(team["clinic"], Membership.Role.ACCOUNTANT)
    api = APIClient()
    api.force_authenticate(user=accountant)
    assert api.get("/api/v1/consultations/").status_code == 403
