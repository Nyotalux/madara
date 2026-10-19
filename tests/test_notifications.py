"""Notifications et rappels : file d'attente, tâches de fond, web et API (jalon 5)."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

import pytest
from django.core import mail
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from apps.appointments import services
from apps.appointments.models import Appointment
from apps.notifications.models import Notification
from apps.notifications.providers import send_email, send_whatsapp
from apps.notifications.services import (
    cancel_appointment_reminders,
    dispatch_pending,
    queue_notification,
    schedule_appointment_reminders,
    send_imminent_reminders,
    unread_count,
)
from apps.patients.models import Patient

from .factories import make_staff  # noqa: F401 (fabriques partagées)

pytestmark = pytest.mark.django_db

STATUS = Notification.Status
CHANNELS = Notification.Channel


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_patient(clinic, **kwargs):
    defaults = {
        "clinic": clinic,
        "first_name": "Yasmine",
        "last_name": "Idrissi",
        "phone": "+212612345678",
        "email": "yasmine@example.ma",
    }
    defaults.update(kwargs)
    return Patient.objects.create(**defaults)


def next_open_day(clinic_tz=None, *, offset=1) -> date:
    day = timezone.localtime(timezone.now(), clinic_tz).date() + timedelta(days=offset)
    while day.weekday() in (5, 6):
        day += timedelta(days=1)
    return day


def at(day: date, hour: int, minute: int = 0):
    return timezone.make_aware(datetime.combine(day, time(hour, minute)))


def book(clinic, patient, membership, *, day=None, user=None) -> Appointment:
    """Rendez-vous à venir (> 24 h) via le service (rappels planifiés)."""
    day = day or next_open_day(offset=3)
    return services.book_appointment(
        clinic=clinic,
        patient=patient,
        practitioner=membership,
        start_at=at(day, 10),
        user=user,
    )


@pytest.fixture
def doctor_membership(clinic, doctor):
    return doctor.memberships.get(clinic=clinic)


@pytest.fixture
def patient(clinic):
    return make_patient(clinic)


# ---------------------------------------------------------------------------
# Mise en file et envoi
# ---------------------------------------------------------------------------


class TestQueue:
    def test_notification_in_app_est_mise_en_attente(self, clinic, doctor):
        notice = queue_notification(
            clinic=clinic,
            recipient=doctor,
            channel=CHANNELS.IN_APP,
            title="Bonjour",
            body="Un message de test.",
        )
        assert notice.pk and notice.status == STATUS.PENDING
        assert notice.scheduled_for is None and notice.is_due

    def test_sans_contact_le_canal_patient_est_ignoré(self, clinic, patient):
        patient.phone = ""
        patient.email = ""
        patient.save()
        assert (
            queue_notification(
                clinic=clinic,
                patient=patient,
                channel=CHANNELS.WHATSAPP,
                title="Rappel",
                body="Bonjour.",
            )
            is None
        )
        assert (
            queue_notification(
                clinic=clinic,
                patient=patient,
                channel=CHANNELS.EMAIL,
                title="Rappel",
                body="Bonjour.",
            )
            is None
        )

    def test_unread_count_compte_les_messages_non_lus(self, clinic, doctor, nurse):
        queue_notification(
            clinic=clinic,
            recipient=doctor,
            channel=CHANNELS.IN_APP,
            title="A",
            body="…",
        )
        queue_notification(
            clinic=clinic,
            recipient=doctor,
            channel=CHANNELS.IN_APP,
            title="B",
            body="…",
        )
        queue_notification(
            clinic=clinic,
            recipient=nurse,
            channel=CHANNELS.IN_APP,
            title="C",
            body="…",
        )
        assert unread_count(doctor) == 2
        assert unread_count(nurse) == 1


class TestProviders:
    def test_email_part_via_le_backend_locmem(self, clinic):
        ok, _ = send_email(
            subject="Bonjour", body="Test d'envoi.", to_email="dest@example.ma"
        )
        assert ok
        assert len(mail.outbox) == 1
        assert mail.outbox[0].to == ["dest@example.ma"]

    def test_whatsapp_console_journe_sans_echec(self):
        ok, _ = send_whatsapp(body="Rappel.", phone="+212612345678")
        assert ok

    def test_whatsapp_sans_numero_echoue(self):
        ok, _ = send_whatsapp(body="Rappel.", phone="")
        assert not ok


class TestDispatch:
    def test_dispatch_envoie_les_notifications_dues(self, clinic, patient):
        queue_notification(
            clinic=clinic,
            patient=patient,
            channel=CHANNELS.EMAIL,
            title="Rappel J-1",
            body="À demain.",
        )
        summary = dispatch_pending()
        assert summary["sent"] == 1 and summary["failed"] == 0
        assert len(mail.outbox) == 1
        assert not Notification.objects.filter(status=STATUS.PENDING).exists()

    def test_les_notifications_futures_ne_partent_pas(self, clinic, patient):
        queue_notification(
            clinic=clinic,
            patient=patient,
            channel=CHANNELS.EMAIL,
            title="Plus tard",
            body="…",
            scheduled_for=timezone.now() + timedelta(days=2),
        )
        summary = dispatch_pending()
        assert summary["sent"] == 0 and summary["remaining"] == 1

    def test_un_echec_est_conserve_pour_nouvelle_tentative(
        self, clinic, patient, monkeypatch
    ):
        from apps.notifications import providers

        queue_notification(
            clinic=clinic,
            patient=patient,
            channel=CHANNELS.EMAIL,
            title="Imposible",
            body="…",
        )
        monkeypatch.setattr(providers, "send_email", lambda **kw: (False, "smtp éteint"))
        summary = dispatch_pending()
        assert summary["failed"] == 1
        notice = Notification.objects.get()
        assert notice.status == STATUS.FAILED
        assert "smtp éteint" in notice.last_error

    def test_le_rappel_envoye_marque_le_rendez_vous(
        self, clinic, doctor_membership, patient
    ):
        appointment = book(clinic, patient, doctor_membership)
        queue_notification(
            clinic=clinic,
            patient=patient,
            appointment=appointment,
            channel=CHANNELS.WHATSAPP,
            category=Notification.Category.APPOINTMENT_REMINDER,
            title="Rappel",
            body="À demain.",
        )
        assert appointment.reminder_sent_at is None
        dispatch_pending()
        appointment.refresh_from_db()
        assert appointment.reminder_sent_at is not None


# ---------------------------------------------------------------------------
# Rappels de rendez-vous
# ---------------------------------------------------------------------------


class TestReminders:
    def test_la_reservation_planifie_les_rappels(
        self, clinic, doctor_membership, patient
    ):
        appointment = book(clinic, patient, doctor_membership)
        rows = Notification.objects.filter(appointment=appointment)
        # J1 (WhatsApp + e-mail patient), H2 (in-app praticien), H1 (in-app + e-mail).
        assert rows.count() == 5
        assert set(rows.values_list("category", flat=True)) == {
            Notification.Category.APPOINTMENT_REMINDER
        }
        channels = set(rows.values_list("channel", flat=True))
        assert channels == {CHANNELS.WHATSAPP, CHANNELS.EMAIL, CHANNELS.IN_APP}
        assert rows.exclude(recipient=None).count() == 2  # praticien, 2 rappels in-app
        # Aucun rappel dû tout de suite.
        dispatch_pending()
        assert rows.filter(status=STATUS.SENT).count() == 0

    def test_les_moments_passes_ne_sont_pas_planifiés(
        self, clinic, doctor_membership, patient
    ):
        appointment = book(clinic, patient, doctor_membership)
        appointment.start_at = timezone.now() + timedelta(hours=2)
        appointment.save(update_fields=["start_at", "end_at", "updated_at"])
        rows = schedule_appointment_reminders(appointment)
        assert rows and all(n.scheduled_for <= appointment.start_at for n in rows)

    def test_annulation_retrait_la_file(self, clinic, doctor_membership, patient):
        appointment = book(clinic, patient, doctor_membership)
        services.set_status(
            appointment, Appointment.Status.CANCELLED, reason="patient absent"
        )
        assert not Notification.objects.filter(
            appointment=appointment, status=STATUS.PENDING
        ).exists()
        assert (
            Notification.objects.filter(
                appointment=appointment, status=STATUS.CANCELLED
            ).count()
            == 5
        )

    def test_annulation_directe_fonctionne_aussi(
        self, clinic, doctor_membership, patient
    ):
        appointment = book(clinic, patient, doctor_membership)
        assert cancel_appointment_reminders(appointment) == 5

    def test_deplacement_replanifie_les_rappels(self, clinic, doctor_membership, patient):
        appointment = book(clinic, patient, doctor_membership)
        new_day = next_open_day(offset=5)
        services.reschedule(appointment, start_at=at(new_day, 14))
        # Les anciens rappels sont retirés ; les cinq nouveaux visent le
        # nouveau créneau (tous les moments deviennent futurs).
        rows = Notification.objects.filter(appointment=appointment, status=STATUS.PENDING)
        assert rows.count() == 5
        assert all(
            (row.scheduled_for - appointment.start_at).total_seconds() < 0 for row in rows
        )


class TestImminent:
    def test_rappele_les_rendez_vous_sans_couverture(
        self, clinic, doctor_membership, patient
    ):
        appointment = Appointment.objects.create(
            clinic=clinic,
            patient=patient,
            practitioner=doctor_membership,
            start_at=timezone.now() + timedelta(minutes=40),
            end_at=timezone.now() + timedelta(minutes=60),
            status=Appointment.Status.CONFIRMED,
        )
        queued = send_imminent_reminders()
        assert queued["queued"] >= 1
        notice = Notification.objects.filter(
            appointment=appointment, channel=CHANNELS.IN_APP
        ).get()
        assert notice.status == STATUS.PENDING
        # Un e-mail patient a été dérivé (adresse connue).
        assert Notification.objects.filter(
            appointment=appointment, channel=CHANNELS.EMAIL
        ).exists()

    def test_les_rendez_vous_deja_couverts_ne_sont_pas_re_rappeles(
        self, clinic, doctor_membership, patient
    ):
        book(clinic, patient, doctor_membership)  # rappels planifiés (futurs)
        assert send_imminent_reminders()["queued"] == 0


class TestTasks:
    def test_digest_de_l_agenda_journalise_pour_le_praticien(
        self, clinic, doctor_membership, patient
    ):
        from apps.appointments.tasks import send_daily_agenda

        # Rendez-vous dans la journée en cours, pendant les horaires.
        start = timezone.now()
        while start.weekday() in (5, 6):
            start += timedelta(days=1)
        Appointment.objects.create(
            clinic=clinic,
            patient=patient,
            practitioner=doctor_membership,
            start_at=start,
            end_at=start + timedelta(minutes=20),
            status=Appointment.Status.CONFIRMED,
        )
        summary = send_daily_agenda()
        assert summary["notices"] >= 2  # in-app + e-mail
        notice = Notification.objects.filter(
            recipient=doctor_membership.user,
            category=Notification.Category.AGENDA_DIGEST,
            channel=CHANNELS.IN_APP,
        ).get()
        assert "rendez-vous" in notice.body.lower()

    def test_tache_de_maintenance_existe_et_s_execute(self):
        from apps.notifications.tasks import dispatch_pending as task

        assert task.name == "notifications.dispatch_pending"
        assert task() == {"sent": 0, "failed": 0, "webpush": 0, "remaining": 0}


# ---------------------------------------------------------------------------
# Web
# ---------------------------------------------------------------------------


class TestWeb:
    def test_le_casier_affiche_les_messages_du_personnel(
        self, client, clinic, doctor, nurse
    ):
        queue_notification(
            clinic=clinic,
            recipient=doctor,
            channel=CHANNELS.IN_APP,
            title="Votre rappel",
            body="Patient à venir.",
            category=Notification.Category.APPOINTMENT_REMINDER,
        )
        queue_notification(
            clinic=clinic,
            recipient=nurse,
            channel=CHANNELS.IN_APP,
            title="Message d'un autre",
            body="Ne doit pas apparaître.",
        )
        client.force_login(doctor)
        response = client.get(reverse("web:notification-list"))
        assert response.status_code == 200
        body = response.content.decode()
        assert "Votre rappel" in body
        assert "Message d'un autre" not in body

    def test_marquer_lu_renseigne_le_champ_et_le_badge(self, client, clinic, doctor):
        notice = queue_notification(
            clinic=clinic,
            recipient=doctor,
            channel=CHANNELS.IN_APP,
            title="À lire",
            body="…",
        )
        client.force_login(doctor)
        response = client.get(reverse("web:notification-list"))
        assert 'class="badge badge--alert"' in response.content.decode()

        response = client.post(
            reverse("web:notification-read", args=[notice.pk]),
            {"next": reverse("web:notification-list")},
        )
        assert response.status_code in (200, 302)
        notice.refresh_from_db()
        assert notice.read_at is not None
        assert unread_count(doctor) == 0

    def test_seul_le_destinataire_peut_marquer_lu(self, client, clinic, doctor, nurse):
        notice = queue_notification(
            clinic=clinic,
            recipient=nurse,
            channel=CHANNELS.IN_APP,
            title="À la nurse",
            body="…",
        )
        client.force_login(doctor)
        response = client.post(
            reverse("web:notification-read", args=[notice.pk]), follow=True
        )
        assert response.status_code == 404

    def test_tout_marquer_comme_lu(self, client, clinic, doctor):
        for title in ("A", "B", "C"):
            queue_notification(
                clinic=clinic,
                recipient=doctor,
                channel=CHANNELS.IN_APP,
                title=title,
                body="…",
            )
        client.force_login(doctor)
        client.post(reverse("web:notification-read-all"))
        assert unread_count(doctor) == 0


# ---------------------------------------------------------------------------
# API mobile
# ---------------------------------------------------------------------------


def api_client(user) -> APIClient:
    api = APIClient()
    api.force_authenticate(user=user)
    return api


class TestApi:
    def test_liste_restreinte_a_ses_notifications(self, clinic, doctor, nurse):
        mine = queue_notification(
            clinic=clinic,
            recipient=doctor,
            channel=CHANNELS.IN_APP,
            title="À moi",
            body="…",
        )
        queue_notification(
            clinic=clinic,
            recipient=nurse,
            channel=CHANNELS.IN_APP,
            title="Pas à moi",
            body="…",
        )
        response = api_client(doctor).get("/api/v1/notifications/")
        assert response.status_code == 200
        ids = [row["id"] for row in response.json()["results"]]
        assert ids == [mine.pk]
        row = response.json()["results"][0]
        assert row["channel"] == CHANNELS.IN_APP
        assert row["is_unread"] is True

    def test_lire_une_notification(self, clinic, doctor):
        notice = queue_notification(
            clinic=clinic,
            recipient=doctor,
            channel=CHANNELS.IN_APP,
            title="À lire",
            body="…",
        )
        response = api_client(doctor).post(f"/api/v1/notifications/{notice.pk}/read/")
        assert response.status_code == 200
        assert response.json()["read_at"]

    def test_les_notifications_d_autrui_sont_invisibles(self, clinic, doctor, nurse):
        notice = queue_notification(
            clinic=clinic,
            recipient=nurse,
            channel=CHANNELS.IN_APP,
            title="Privé",
            body="…",
        )
        api = api_client(doctor)
        assert api.get(f"/api/v1/notifications/{notice.pk}/").status_code == 404
        assert api.post(f"/api/v1/notifications/{notice.pk}/read/").status_code == 404

    def test_tout_lire_via_l_api(self, clinic, doctor):
        for title in ("A", "B"):
            queue_notification(
                clinic=clinic,
                recipient=doctor,
                channel=CHANNELS.IN_APP,
                title=title,
                body="…",
            )
        response = api_client(doctor).post("/api/v1/notifications/read_all/")
        assert response.status_code == 200
        assert response.json()["updated"] == 2

    def test_les_rappels_patients_ne_sont_pas_exposes(
        self, clinic, doctor_membership, patient
    ):
        book(clinic, patient, doctor_membership)
        response = api_client(doctor_membership.user).get("/api/v1/notifications/")
        # Seuls les messages in-app incombent au praticien (5 lignes, 2 in-app).
        assert all(
            row["channel"] == CHANNELS.IN_APP for row in response.json()["results"]
        )
        assert len(response.json()["results"]) == 2

    def test_le_cloisonnement_s_applique_entre_cliniques(
        self, clinic, other_clinic, doctor
    ):
        visible = queue_notification(
            clinic=clinic,
            recipient=doctor,
            channel=CHANNELS.IN_APP,
            title="Clinique courante",
            body="…",
        )
        queue_notification(
            clinic=other_clinic,
            recipient=doctor,
            channel=CHANNELS.IN_APP,
            title="Autre clinique",
            body="…",
        )
        response = api_client(doctor).get("/api/v1/notifications/")
        assert response.status_code == 200
        ids = [row["id"] for row in response.json()["results"]]
        assert ids == [visible.pk]
