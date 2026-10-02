"""Agenda et rendez-vous : règle métier, web et API (jalon 3)."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

import pytest
from django.core.exceptions import ValidationError
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import BlockedSlot, Membership, OpeningHour
from apps.appointments import services
from apps.appointments.models import Appointment
from apps.common.context import clinic_context
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


def next_open_day(clinic_tz=None, *, offset=1) -> date:
    """Prochain jour ouvré (les tests n'ouvrent pas le week-end)."""
    day = timezone.localtime(timezone.now(), clinic_tz).date() + timedelta(days=offset)
    while day.weekday() in (5, 6):
        day += timedelta(days=1)
    return day


def next_weekday(weekday: int) -> date:
    """Prochaine occurrence d'un jour de semaine donné (0 = lundi)."""
    day = next_open_day()
    while day.weekday() != weekday:
        day += timedelta(days=1)
    return day


def at(day: date, hour: int, minute: int = 0):
    return timezone.make_aware(datetime.combine(day, time(hour, minute)))


@pytest.fixture
def team(clinic, other_clinic):
    """Deux médecins, un infirmier, une reception, une seconde clinique."""
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
        return services.book_appointment(
            clinic=clinic,
            patient=patient,
            practitioner=membership,
            start_at=at(day, hour, minute),
            **kwargs,
        )


# ---------------------------------------------------------------------------
# Création : conflits, horaires, indisponibilités
# ---------------------------------------------------------------------------


def test_book_appointment_assigns_reference_and_duration(team, patient):
    day = next_open_day()
    with clinic_context(team["clinic"]):
        appointment = services.book_appointment(
            clinic=team["clinic"],
            patient=patient,
            practitioner=team["doctor_membership"],
            start_at=at(day, 9),
            user=team["reception"],
        )
    assert appointment.reference.startswith("RDV-")
    assert appointment.status == STATUS.PENDING
    assert appointment.end_at > appointment.start_at
    assert appointment.created_by_id == team["reception"].pk


def test_book_rejects_practitioner_conflict(team, patient):
    day = next_open_day()
    book(team["clinic"], patient, team["doctor_membership"], day, 9)
    other = make_patient(team["clinic"], first_name="Ali", last_name="Amrani")
    with pytest.raises(ValidationError):
        # 09:00–09:20 est occupé : 09:10 est déjà un chevauchement.
        book(team["clinic"], other, team["doctor_membership"], day, 9, 10)


def test_book_rejects_patient_conflict(team, patient):
    day = next_open_day()
    book(team["clinic"], patient, team["doctor_membership"], day, 9)
    # Même patient, autre praticien, heures qui se chevauchent : refusé.
    with pytest.raises(ValidationError):
        book(team["clinic"], patient, team["colleague_membership"], day, 9, 15)


def test_book_rejects_outside_opening_hours(team, patient):
    day = next_open_day()
    with pytest.raises(ValidationError):
        book(team["clinic"], patient, team["doctor_membership"], day, 7)


def test_book_rejects_closed_day(team, patient):
    saturday = next_weekday(5)  # les médecins sont fermés le samedi
    with pytest.raises(ValidationError):
        book(team["clinic"], patient, team["doctor_membership"], saturday, 9)


def test_book_rejects_blocked_slot(team, patient):
    day = next_open_day(offset=2)
    with clinic_context(team["clinic"]):
        BlockedSlot.objects.create(
            membership=team["doctor_membership"],
            start_at=at(day, 9),
            end_at=at(day, 12),
            reason="Réunion de service",
        )
    with pytest.raises(ValidationError) as excinfo:
        book(team["clinic"], patient, team["doctor_membership"], day, 10)
    assert "Réunion de service" in str(excinfo.value)


def test_book_rejects_other_clinic(team, patient):
    day = next_open_day()
    with pytest.raises(ValidationError):
        book(team["other_clinic"], patient, team["rival_membership"], day, 9)


def test_book_rejects_practitioner_from_another_clinic(team, patient):
    day = next_open_day()
    with pytest.raises(ValidationError):
        book(team["clinic"], patient, team["rival_membership"], day, 9)


# ---------------------------------------------------------------------------
# Créneaux disponibles
# ---------------------------------------------------------------------------


def test_available_slots_follow_opening_hours(team):
    day = next_open_day()
    with clinic_context(team["clinic"]):
        slots = services.available_slots(
            team["doctor_membership"], day, clinic=team["clinic"]
        )
    assert slots
    starts = [start for start, _ in slots]
    assert min(starts).time() >= time(9)
    assert max(end for _, end in slots).time() <= time(17)
    # Pas de découpage : durée de consultation du praticien (20 min par défaut).
    step = slots[0][1] - slots[0][0]
    assert step == timedelta(minutes=20)
    assert all(end - start == step for start, end in slots)


def test_available_slots_excludes_busy_and_blocked(team, patient):
    day = next_open_day()
    book(team["clinic"], patient, team["doctor_membership"], day, 10)
    with clinic_context(team["clinic"]):
        slots = services.available_slots(
            team["doctor_membership"], day, clinic=team["clinic"]
        )
    taken = [start for start, _ in slots if start.time() == time(10)]
    assert taken == []


def test_available_slots_empty_when_clinic_closed(team):
    saturday = next_weekday(5)
    with clinic_context(team["clinic"]):
        assert (
            services.available_slots(
                team["doctor_membership"], saturday, clinic=team["clinic"]
            )
            == []
        )


def test_available_slots_ignore_past_by_default(team):
    tz = services.clinic_timezone(team["clinic"])
    today = timezone.localtime(timezone.now(), tz).date()
    with clinic_context(team["clinic"]):
        future = services.available_slots(
            team["doctor_membership"], today, clinic=team["clinic"]
        )
        past = services.available_slots(
            team["doctor_membership"],
            today,
            clinic=team["clinic"],
            include_past=True,
        )
    assert len(past) >= len(future)


# ---------------------------------------------------------------------------
# Statuts, annulation, déplacement
# ---------------------------------------------------------------------------


def test_transition_sequence_and_journal(team, patient):
    day = next_open_day()
    appointment = book(
        team["clinic"],
        patient,
        team["doctor_membership"],
        day,
        9,
        status=STATUS.CONFIRMED,
    )
    services.set_status(appointment, STATUS.ARRIVED)
    services.set_status(appointment, STATUS.IN_PROGRESS)
    services.set_status(appointment, STATUS.DONE)
    appointment.refresh_from_db()
    assert appointment.status == STATUS.DONE
    assert appointment.status in Appointment.TERMINAL_STATUSES
    # Une consultation terminée est définitive.
    assert not appointment.can_transition_to(STATUS.CONFIRMED)
    assert not appointment.can_be_cancelled


def test_invalid_transition_is_refused(team, patient):
    day = next_open_day()
    appointment = book(team["clinic"], patient, team["doctor_membership"], day, 9)
    with pytest.raises(ValidationError):
        services.set_status(appointment, STATUS.DONE)
    appointment.refresh_from_db()
    assert appointment.status == STATUS.PENDING


def test_cancel_records_journal_and_frees_slot(team, patient):
    day = next_open_day()
    appointment = book(team["clinic"], patient, team["doctor_membership"], day, 9)
    appointment.cancel("Patient injoignable", user=team["reception"])

    appointment.refresh_from_db()
    assert appointment.is_cancelled
    assert appointment.cancellation_reason == "Patient injoignable"
    assert appointment.cancelled_at is not None

    entry = appointment.cancellations.get()
    assert entry.reason == "Patient injoignable"
    assert entry.cancelled_by_id == team["reception"].pk

    with clinic_context(team["clinic"]):
        slots = services.available_slots(
            team["doctor_membership"], day, clinic=team["clinic"]
        )
    assert any(start.hour == 9 for start, _ in slots)


def test_reschedule_detects_conflict(team, patient):
    day = next_open_day()
    first = book(team["clinic"], patient, team["doctor_membership"], day, 9)
    book(
        team["clinic"],
        make_patient(team["clinic"], first_name="Zakaria", last_name="Bouzid"),
        team["doctor_membership"],
        day,
        14,
    )
    with pytest.raises(ValidationError):
        services.reschedule(first, start_at=at(day, 14), end_at=at(day, 14, 30))
    services.reschedule(first, start_at=at(day, 11), end_at=at(day, 11, 30))
    first.refresh_from_db()
    assert first.start_at.hour == 11


def test_agenda_queryset_scopes_clinic_and_status(team, patient):
    day = next_open_day()
    mine = book(team["clinic"], patient, team["doctor_membership"], day, 9)
    cancelled = book(
        team["clinic"],
        make_patient(team["clinic"], first_name="Hicham", last_name="Ouazzani"),
        team["colleague_membership"],
        day,
        11,
    )
    cancelled.cancel("Annulé", user=team["reception"])

    start, end = services.day_bounds(day)
    visible = services.agenda_queryset(team["clinic"], start=start, end=end)
    assert list(visible) == [mine]

    with_cancelled = services.agenda_queryset(
        team["clinic"], start=start, end=end, with_cancelled=True
    )
    assert with_cancelled.count() == 2

    other = services.agenda_queryset(team["other_clinic"], start=start, end=end)
    assert other.count() == 0


# ---------------------------------------------------------------------------
# Web
# ---------------------------------------------------------------------------


def test_agenda_pages_render(client, team, patient):
    day = next_open_day()
    appointment = book(team["clinic"], patient, team["doctor_membership"], day, 9)
    client.force_login(team["reception"])

    response = client.get(reverse("web:agenda"), {"date": day.isoformat()})
    assert response.status_code == 200
    assert f"/rendez-vous/{appointment.pk}/".encode() in response.content
    assert patient.display_name.encode() in response.content

    assert (
        client.get(reverse("web:agenda-week"), {"date": day.isoformat()}).status_code
        == 200
    )
    assert client.get(reverse("web:waiting-room")).status_code == 200
    assert client.get(reverse("web:appointment-list")).status_code == 200
    assert (
        client.get(reverse("web:appointment-detail", args=[appointment.pk])).status_code
        == 200
    )
    slots = client.get(
        reverse("web:appointment-slots"),
        {"practitioner": team["doctor_membership"].pk, "day": day.isoformat()},
    )
    assert slots.status_code == 200
    assert b"id_slot" in slots.content


def test_accountant_is_refused(client, team):
    accountant, _ = make_staff(team["clinic"], Membership.Role.ACCOUNTANT)
    client.force_login(accountant)
    assert client.get(reverse("web:agenda")).status_code == 403


def test_doctor_sees_only_own_agenda(client, team, patient):
    day = next_open_day()
    mine = book(team["clinic"], patient, team["doctor_membership"], day, 9)
    colleague = book(
        team["clinic"],
        make_patient(team["clinic"], first_name="Nabil", last_name="Cherkaoui"),
        team["colleague_membership"],
        day,
        10,
    )
    client.force_login(team["doctor"])
    content = client.get(reverse("web:agenda"), {"date": day.isoformat()}).content
    assert f"/rendez-vous/{mine.pk}/".encode() in content
    assert f"/rendez-vous/{colleague.pk}/".encode() not in content
    assert (
        client.get(reverse("web:appointment-detail", args=[colleague.pk])).status_code
        == 404
    )


def test_create_appointment_from_web(client, team, patient):
    day = next_open_day()
    client.force_login(team["reception"])
    response = client.post(
        reverse("web:appointment-create"),
        {
            "patient": patient.pk,
            "practitioner": team["doctor_membership"].pk,
            "day": day.isoformat(),
            "slot": "",
            "kind": Appointment.Kind.CONSULTATION,
            "status": STATUS.CONFIRMED,
            "reason": "Fièvre",
        },
    )
    assert response.status_code == 302
    appointment = Appointment.objects.get(reason="Fièvre")
    assert appointment.patient_id == patient.pk
    assert appointment.status == STATUS.CONFIRMED
    assert appointment.start_at.date() == day


def test_create_appointment_rejects_taken_slot(client, team, patient):
    day = next_open_day()
    first = book(team["clinic"], patient, team["doctor_membership"], day, 9)
    client.force_login(team["reception"])
    response = client.post(
        reverse("web:appointment-create"),
        {
            "patient": make_patient(team["clinic"]).pk,
            "practitioner": team["doctor_membership"].pk,
            "day": day.isoformat(),
            "slot": first.start_at.strftime("%Y-%m-%dT%H:%M"),
            "kind": Appointment.Kind.CONSULTATION,
            "status": STATUS.CONFIRMED,
        },
    )
    assert response.status_code == 200
    assert not Appointment.objects.filter(reason="").exclude(pk=first.pk).exists()
    assert (
        b"disponible" in response.content.lower() or b"pris" in response.content.lower()
    )


def test_doctor_cannot_book_for_a_colleague(client, team, patient):
    day = next_open_day()
    client.force_login(team["doctor"])
    response = client.post(
        reverse("web:appointment-create"),
        {
            "patient": patient.pk,
            "practitioner": team["colleague_membership"].pk,
            "day": day.isoformat(),
            "slot": "",
            "kind": Appointment.Kind.CONSULTATION,
            "status": STATUS.CONFIRMED,
        },
    )
    assert response.status_code == 302
    appointment = Appointment.objects.get(patient=patient)
    assert appointment.practitioner_id == team["doctor_membership"].pk


def test_quick_status_updates_waiting_room(client, team, patient):
    day = next_open_day()
    appointment = book(
        team["clinic"],
        patient,
        team["doctor_membership"],
        day,
        9,
        status=STATUS.CONFIRMED,
    )
    client.force_login(team["reception"])
    response = client.post(
        reverse("web:appointment-quick-status", args=[appointment.pk, STATUS.ARRIVED])
    )
    assert response.status_code == 302
    appointment.refresh_from_db()
    assert appointment.status == STATUS.ARRIVED


def test_status_page_refuses_impossible_transition(client, team, patient):
    day = next_open_day()
    appointment = book(team["clinic"], patient, team["doctor_membership"], day, 9)
    client.force_login(team["reception"])
    response = client.post(
        reverse("web:appointment-status", args=[appointment.pk]),
        {"status": STATUS.DONE},
    )
    assert response.status_code == 200
    appointment.refresh_from_db()
    assert appointment.status == STATUS.PENDING


def test_status_page_cancels_with_reason(client, team, patient):
    day = next_open_day()
    appointment = book(team["clinic"], patient, team["doctor_membership"], day, 9)
    client.force_login(team["reception"])
    client.post(
        reverse("web:appointment-status", args=[appointment.pk]),
        {"status": STATUS.CANCELLED, "reason": "Empêchement"},
    )
    appointment.refresh_from_db()
    assert appointment.is_cancelled
    assert appointment.cancellations.get().reason == "Empêchement"


def test_cross_clinic_appointment_is_not_reachable(client, team, patient):
    day = next_open_day()
    appointment = book(team["clinic"], patient, team["doctor_membership"], day, 9)
    client.force_login(team["rival"])
    response = client.get(reverse("web:appointment-detail", args=[appointment.pk]))
    assert response.status_code == 404


def test_patient_detail_lists_appointments(client, team, patient):
    day = next_open_day()
    appointment = book(team["clinic"], patient, team["doctor_membership"], day, 9)
    client.force_login(team["reception"])
    response = client.get(reverse("web:patient-detail", args=[patient.pk]))
    assert response.status_code == 200
    assert f"/rendez-vous/{appointment.pk}/".encode() in response.content
    assert "Aucun rendez-vous à venir".encode() not in response.content


# ---------------------------------------------------------------------------
# Admin
# ---------------------------------------------------------------------------


def test_admin_pages_render(client, team, patient):
    day = next_open_day()
    appointment = book(team["clinic"], patient, team["doctor_membership"], day, 9)
    platform_admin = UserFactory.create(
        email="superadmin@example.ma",
        is_staff=True,
        is_superuser=True,
        is_platform_staff=True,
    )
    client.force_login(platform_admin)
    assert client.get("/admin/appointments/appointment/").status_code == 200
    assert (
        client.get(
            f"/admin/appointments/appointment/{appointment.pk}/change/"
        ).status_code
        == 200
    )
    assert client.get("/admin/appointments/appointmentcancellation/").status_code == 200


def test_admin_confirm_action(client, team, patient):
    day = next_open_day()
    appointment = book(team["clinic"], patient, team["doctor_membership"], day, 9)
    platform_admin = UserFactory.create(
        email="superadmin2@example.ma",
        is_staff=True,
        is_superuser=True,
        is_platform_staff=True,
    )
    client.force_login(platform_admin)
    response = client.post(
        "/admin/appointments/appointment/",
        {
            "action": "confirm_selected",
            "_selected_action": [str(appointment.pk)],
        },
        follow=True,
    )
    assert response.status_code == 200
    appointment.refresh_from_db()
    assert appointment.status == STATUS.CONFIRMED


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------


@pytest.fixture
def api(team):
    client = APIClient()
    client.force_authenticate(user=team["reception"])
    return client


def test_api_lists_only_current_clinic(api, team, patient):
    day = next_open_day()
    mine = book(team["clinic"], patient, team["doctor_membership"], day, 9)
    book(
        team["other_clinic"],
        make_patient(team["other_clinic"]),
        team["rival_membership"],
        day,
        9,
    )
    response = api.get("/api/v1/appointments/", {"date": day.isoformat()})
    assert response.status_code == 200
    payload = response.json()["results"]
    assert [row["reference"] for row in payload] == [mine.reference]
    assert payload[0]["patient_name"] == patient.display_name


def test_api_doctor_sees_own_appointments(team, patient):
    day = next_open_day()
    book(team["clinic"], patient, team["doctor_membership"], day, 9)
    book(
        team["clinic"],
        make_patient(team["clinic"], first_name="Rim", last_name="Sabri"),
        team["colleague_membership"],
        day,
        10,
    )
    api = APIClient()
    api.force_authenticate(user=team["doctor"])
    payload = api.get("/api/v1/appointments/", {"date": day.isoformat()}).json()[
        "results"
    ]
    assert len(payload) == 1
    assert payload[0]["practitioner"] == team["doctor_membership"].pk


def test_api_create_and_conflict(team, patient):
    day = next_open_day()
    api = APIClient()
    api.force_authenticate(user=team["reception"])
    payload = {
        "patient": patient.pk,
        "practitioner": team["doctor_membership"].pk,
        "start_at": at(day, 9).isoformat(),
        "end_at": at(day, 9, 30).isoformat(),
        "kind": Appointment.Kind.CONSULTATION,
        "status": STATUS.CONFIRMED,
        "reason": "Douleur",
    }
    created = api.post("/api/v1/appointments/", payload, format="json")
    assert created.status_code == 201, created.json()
    appointment = Appointment.objects.get(reason="Douleur")

    duplicate = api.post(
        "/api/v1/appointments/",
        {**payload, "patient": make_patient(team["clinic"]).pk},
        format="json",
    )
    assert duplicate.status_code == 400
    assert Appointment.objects.filter(reason="Douleur").count() == 1
    assert appointment.reference


def test_api_status_and_cancel(team, patient):
    day = next_open_day()
    appointment = book(
        team["clinic"],
        patient,
        team["doctor_membership"],
        day,
        9,
        status=STATUS.CONFIRMED,
    )
    api = APIClient()
    api.force_authenticate(user=team["reception"])

    response = api.post(
        f"/api/v1/appointments/{appointment.pk}/status/",
        {"status": STATUS.ARRIVED},
        format="json",
    )
    assert response.status_code == 200
    assert response.json()["status"] == STATUS.ARRIVED

    response = api.post(
        f"/api/v1/appointments/{appointment.pk}/cancel/",
        {"reason": "Annulé par le patient"},
        format="json",
    )
    assert response.status_code == 200
    appointment.refresh_from_db()
    assert appointment.is_cancelled
    assert appointment.cancellations.get().reason == "Annulé par le patient"


def test_api_availability_and_patient_search(team, patient):
    day = next_open_day()
    api = APIClient()
    api.force_authenticate(user=team["reception"])
    response = api.get(
        "/api/v1/appointments/availability/",
        {"date": day.isoformat(), "practitioner": team["doctor_membership"].pk},
    )
    assert response.status_code == 200
    slots = response.json()["practitioners"][0]["slots"]
    assert slots and "label" in slots[0]

    response = api.get("/api/v1/appointments/patients/", {"q": patient.last_name})
    assert response.status_code == 200
    assert response.json()[0]["id"] == patient.pk


def test_api_refuses_accountant(team):
    accountant, _ = make_staff(team["clinic"], Membership.Role.ACCOUNTANT)
    api = APIClient()
    api.force_authenticate(user=accountant)
    assert api.get("/api/v1/appointments/").status_code == 403


def test_api_ignores_other_clinic_appointment(team, patient):
    day = next_open_day()
    appointment = book(team["clinic"], patient, team["doctor_membership"], day, 9)
    api = APIClient()
    api.force_authenticate(user=team["rival"])
    assert api.get(f"/api/v1/appointments/{appointment.pk}/").status_code == 404


def test_opening_hour_unchanged_by_appointments(team, patient):
    """Prendre un rendez-vous ne touche pas aux horaires du praticien."""
    day = next_open_day()
    book(team["clinic"], patient, team["doctor_membership"], day, 9)
    with clinic_context(team["clinic"]):
        assert (
            OpeningHour.objects.filter(membership=team["doctor_membership"]).count() == 6
        )
