"""Facturation : factures, règlements, impayés, web et API (jalon 6)."""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.urls import reverse
from rest_framework.test import APIClient

from apps.billing import services
from apps.billing.models import Invoice, InvoiceLine, Payment
from apps.patients.models import Patient

pytestmark = pytest.mark.django_db

STATUS = Invoice.Status


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


def make_invoice(clinic, patient, *, status=STATUS.ISSUED, tax="0.20", **kwargs):
    """Facture de deux lignes (100 + 50) via le service, émise par défaut."""
    lines = kwargs.pop(
        "lines",
        [
            {"label": "Consultation", "quantity": 1, "unit_price": "100.00"},
            {"label": "Pansement", "quantity": 2, "unit_price": "25.00"},
        ],
    )
    return services.create_invoice(
        clinic=clinic,
        patient=patient,
        lines=lines,
        tax_rate=Decimal(tax),
        status=status,
        **kwargs,
    )


def api_client(user) -> APIClient:
    api = APIClient()
    api.force_authenticate(user=user)
    return api


@pytest.fixture
def accountant_membership(clinic, accountant):
    return accountant.memberships.get(clinic=clinic)


@pytest.fixture
def patient(clinic):
    return make_patient(clinic)


@pytest.fixture
def other_patient(other_clinic):
    return make_patient(other_clinic)


# ---------------------------------------------------------------------------
# Création et montants
# ---------------------------------------------------------------------------


class TestCreation:
    def test_la_facture_porte_une_reference_par_clinique(self, clinic, patient):
        invoice = make_invoice(clinic, patient, status=STATUS.DRAFT)
        assert invoice.reference.startswith("FAC-")
        assert InvoiceLine.objects.filter(invoice=invoice).count() == 2

    def test_reference_unique_par_clinique(
        self, clinic, other_clinic, patient, other_patient
    ):
        first = make_invoice(clinic, patient, status=STATUS.DRAFT)
        second = make_invoice(clinic, patient, status=STATUS.DRAFT)
        assert first.reference != second.reference
        # Deux cliniques peuvent numéroter en parallèle.
        assert make_invoice(other_clinic, other_patient, status=STATUS.DRAFT).reference

    def test_les_montants_sont_calcules_a_la_lecture(self, clinic, patient):
        invoice = make_invoice(clinic, patient, tax="0.20")
        assert invoice.amount_excl_tax == Decimal("150.00")
        assert invoice.tax_amount == Decimal("30.00")
        assert invoice.total == Decimal("180.00")
        assert invoice.amount_due == Decimal("180.00")

    def test_lignes_vides_refusees(self, clinic, patient):
        with pytest.raises(ValidationError):
            services.create_invoice(
                clinic=clinic,
                patient=patient,
                lines=[{"label": "", "quantity": 1, "unit_price": "10"}],
            )

    def test_patient_d_autre_clinique_refuse(self, clinic, other_clinic, other_patient):
        with pytest.raises(ValidationError):
            make_invoice(clinic, other_patient, status=STATUS.DRAFT)

    def test_quantites_positives_exigees(self, clinic, patient):
        with pytest.raises(ValidationError):
            services.create_invoice(
                clinic=clinic,
                patient=patient,
                lines=[
                    {
                        "label": "Acte",
                        "quantity": "0",
                        "unit_price": "50.00",
                    }
                ],
            )


# ---------------------------------------------------------------------------
# Cycle de vie : émission, règlement, annulation
# ---------------------------------------------------------------------------


class TestLifecycle:
    def test_emission_fixe_l_echeance(self, clinic, patient):
        invoice = make_invoice(clinic, patient, status=STATUS.DRAFT)
        assert invoice.due_at is None
        services.issue_invoice(invoice, due_days=15)
        invoice.refresh_from_db()
        assert invoice.status == STATUS.ISSUED
        assert invoice.due_at is not None

    def test_paiement_partiel_passe_en_partiel(self, clinic, patient):
        invoice = make_invoice(clinic, patient)  # total 180
        services.add_payment(invoice, amount=Decimal("80.00"), method=Payment.Method.CASH)
        invoice.refresh_from_db()
        assert invoice.status == STATUS.PARTIAL
        assert invoice.paid_amount == Decimal("80.00")
        assert invoice.amount_due == Decimal("100.00")

    def test_solde_passe_en_payee(self, clinic, patient):
        invoice = make_invoice(clinic, patient)
        services.add_payment(
            invoice, amount=Decimal("180.00"), method=Payment.Method.CARD
        )
        invoice.refresh_from_db()
        assert invoice.status == STATUS.PAID
        assert invoice.amount_due == 0

    def test_paiement_superieur_au_reste_refuse(self, clinic, patient):
        invoice = make_invoice(clinic, patient)
        with pytest.raises(ValidationError):
            services.add_payment(
                invoice, amount=Decimal("500.00"), method=Payment.Method.CASH
            )

    def test_paiement_negatif_ou_nul_refuse(self, clinic, patient):
        invoice = make_invoice(clinic, patient)
        with pytest.raises(ValidationError):
            services.add_payment(invoice, amount=Decimal("0"), method=Payment.Method.CASH)

    def test_reglement_sur_brouillon_refuse(self, clinic, patient):
        invoice = make_invoice(clinic, patient, status=STATUS.DRAFT)
        with pytest.raises(ValidationError):
            services.add_payment(
                invoice, amount=Decimal("50.00"), method=Payment.Method.CASH
            )

    def test_annulation_possible_sans_reglement(self, clinic, patient):
        invoice = make_invoice(clinic, patient)
        services.cancel_invoice(invoice, reason="erreur de facturation")
        invoice.refresh_from_db()
        assert invoice.status == STATUS.CANCELLED
        assert "erreur" in invoice.cancellation_reason

    def test_annulation_refusee_avec_reglement(self, clinic, patient):
        invoice = make_invoice(clinic, patient)
        services.add_payment(invoice, amount=Decimal("50.00"), method=Payment.Method.CASH)
        with pytest.raises(ValidationError):
            services.cancel_invoice(invoice)

    def test_facture_payee_est_definitive(self, clinic, patient):
        invoice = make_invoice(clinic, patient)
        services.add_payment(
            invoice, amount=Decimal("180.00"), method=Payment.Method.CASH
        )
        with pytest.raises(ValidationError):
            services.cancel_invoice(invoice)

    def test_retrait_de_reglement_recalcule(self, clinic, patient):
        invoice = make_invoice(clinic, patient)
        payment = services.add_payment(
            invoice, amount=Decimal("180.00"), method=Payment.Method.CASH
        )
        services.delete_payment(payment)
        invoice.refresh_from_db()
        assert invoice.paid_amount == 0
        assert invoice.status == STATUS.PARTIAL
        assert invoice.amount_due == Decimal("180.00")


# ---------------------------------------------------------------------------
# Impayés
# ---------------------------------------------------------------------------


class TestImpayes:
    def test_factures_echues_listees(self, clinic, patient):
        invoice = make_invoice(clinic, patient)
        invoice.due_at = services.timezone.localdate() - services.timezone.timedelta(
            days=40
        )
        invoice.save(update_fields=["due_at"])
        fresh = make_invoice(clinic, patient)  # échéance future
        overdue = services.overdue_invoices(clinic)
        assert invoice in overdue
        assert fresh not in overdue

    def test_annulee_et_payee_hors_impayes(self, clinic, patient):
        paid = make_invoice(clinic, patient)
        services.add_payment(paid, amount=Decimal("180.00"), method=Payment.Method.CASH)
        cancelled = make_invoice(clinic, patient)
        services.cancel_invoice(cancelled)
        assert not services.overdue_invoices(clinic).exists()

    def test_la_relance_respecte_les_delais_de_la_clinique(
        self, clinic, patient, monkeypatch
    ):
        from apps.billing.tasks import send_overdue_invoice_reminders
        from apps.notifications.models import Notification

        clinic.dunning_days = 10
        clinic.save(update_fields=["dunning_days"])
        invoice = make_invoice(clinic, patient)
        invoice.due_at = services.timezone.localdate() - services.timezone.timedelta(
            days=5  # < dunning_days : pas encore de relance
        )
        invoice.save(update_fields=["due_at"])
        summary = send_overdue_invoice_reminders()
        assert summary["clinics"] == 0

        invoice.due_at = services.timezone.localdate() - services.timezone.timedelta(
            days=10  # première relance
        )
        invoice.save(update_fields=["due_at"])
        summary = send_overdue_invoice_reminders()
        assert summary["clinics"] == 1
        assert Notification.objects.filter(clinic=clinic).exists()

    def test_la_relance_epargne_le_patient_sans_contact(
        self, clinic, patient, accountant, monkeypatch
    ):
        from apps.billing.tasks import send_overdue_invoice_reminders
        from apps.notifications.models import Notification

        patient.phone = ""
        patient.email = ""
        patient.save()
        clinic.dunning_days = 0
        clinic.save(update_fields=["dunning_days"])
        invoice = make_invoice(clinic, patient)
        invoice.due_at = services.timezone.localdate() - services.timezone.timedelta(
            days=7
        )
        invoice.save(update_fields=["due_at"])
        summary = send_overdue_invoice_reminders()
        # Aucune notification patient ; la synthèse in-app au staff subsiste.
        assert summary["patients_relanced"] == 0
        assert Notification.objects.filter(
            clinic=clinic, channel=Notification.Channel.IN_APP
        ).exists()


# ---------------------------------------------------------------------------
# Web
# ---------------------------------------------------------------------------


class TestWeb:
    def test_la_liste_est_reservee_aux_roles_facturation(self, client, clinic, doctor):
        client.force_login(doctor)
        response = client.get(reverse("web:invoice-list"))
        assert response.status_code == 403

    def test_liste_et_fiche_pour_la_comptabilite(
        self, client, clinic, accountant, patient
    ):
        invoice = make_invoice(clinic, patient)
        client.force_login(accountant)
        assert client.get(reverse("web:invoice-list")).status_code == 200
        response = client.get(reverse("web:invoice-detail", args=[invoice.pk]))
        assert response.status_code == 200
        assert invoice.reference in response.content.decode()

    def test_creation_via_le_formulaire_web(self, client, clinic, accountant, patient):
        client.force_login(accountant)
        response = client.post(
            reverse("web:invoice-create"),
            {
                "patient": patient.pk,
                "consultation": "",
                "tax_rate": "0.20",
                "due_days": 30,
                "notes": "",
                "form-TOTAL_FORMS": "1",
                "form-INITIAL_FORMS": "0",
                "form-MIN_NUM_FORMS": "0",
                "form-MAX_NUM_FORMS": "1000",
                "form-0-label": "Consultation",
                "form-0-quantity": "1",
                "form-0-unit_price": "150.00",
            },
        )
        assert response.status_code == 302
        invoice = Invoice.objects.get()
        assert invoice.status == STATUS.DRAFT
        assert invoice.total == Decimal("180.00")

    def test_emission_et_encaissement_web(self, client, clinic, accountant, patient):
        invoice = make_invoice(clinic, patient, status=STATUS.DRAFT)
        client.force_login(accountant)
        response = client.post(reverse("web:invoice-issue", args=[invoice.pk]))
        invoice.refresh_from_db()
        assert response.status_code == 302 and invoice.status == STATUS.ISSUED

        response = client.post(
            reverse("web:invoice-payment", args=[invoice.pk]),
            {
                "amount": "180.00",
                "method": Payment.Method.CASH,
                "paid_at": "2026-10-19T10:00",
                "reference": "espèces #1",
            },
        )
        invoice.refresh_from_db()
        assert response.status_code == 302
        assert invoice.status == STATUS.PAID

    def test_annulation_web_reservee_admin_et_comptabilite(
        self, client, clinic, receptionist, patient
    ):
        invoice = make_invoice(clinic, patient)
        client.force_login(receptionist)
        response = client.post(
            reverse("web:invoice-cancel", args=[invoice.pk]),
            {"reason": "essai"},
        )
        assert response.status_code == 403
        invoice.refresh_from_db()
        assert invoice.status == STATUS.ISSUED


# ---------------------------------------------------------------------------
# API mobile
# ---------------------------------------------------------------------------


class TestApi:
    def test_praticien_exclu_de_la_facturation(self, clinic, doctor, patient):
        make_invoice(clinic, patient)
        response = api_client(doctor).get("/api/v1/invoices/")
        assert response.status_code == 403

    def test_liste_et_creation_via_api(
        self, clinic, accountant, accountant_membership, patient
    ):
        api = api_client(accountant)
        response = api.get("/api/v1/invoices/")
        assert response.status_code == 200

        response = api.post(
            "/api/v1/invoices/",
            {
                "patient": patient.pk,
                "tax_rate": "0.20",
                "emit": True,
                "due_days": 30,
                "lines": [
                    {"label": "Consultation", "quantity": "1", "unit_price": "150.00"}
                ],
            },
            format="json",
        )
        assert response.status_code == 201, response.content
        invoice = Invoice.objects.get()
        assert invoice.status == STATUS.ISSUED
        assert invoice.total == Decimal("180.00")

    def test_encaissement_via_api(
        self, clinic, accountant, accountant_membership, patient
    ):
        invoice = make_invoice(clinic, patient)
        response = api_client(accountant).post(
            f"/api/v1/invoices/{invoice.pk}/pay/",
            {"amount": "100.00", "method": Payment.Method.CARD},
            format="json",
        )
        assert response.status_code == 200
        invoice.refresh_from_db()
        assert invoice.status == STATUS.PARTIAL
        assert response.json()["amount_due"] == "80.00"

    def test_annulation_via_api_refusee_a_l_accueil(self, clinic, receptionist, patient):
        invoice = make_invoice(clinic, patient)
        response = api_client(receptionist).post(
            f"/api/v1/invoices/{invoice.pk}/cancel/",
            {"reason": "erreur"},
            format="json",
        )
        assert response.status_code == 403

    def test_annulation_via_api_comptabilite(self, clinic, accountant, patient):
        invoice = make_invoice(clinic, patient)
        response = api_client(accountant).post(
            f"/api/v1/invoices/{invoice.pk}/cancel/",
            {"reason": "erreur de saisie"},
            format="json",
        )
        assert response.status_code == 200
        invoice.refresh_from_db()
        assert invoice.status == STATUS.CANCELLED

    def test_historique_des_reglements(self, clinic, accountant, patient):
        invoice = make_invoice(clinic, patient)
        services.add_payment(invoice, amount=Decimal("50.00"), method=Payment.Method.CASH)
        response = api_client(accountant).get(f"/api/v1/invoices/{invoice.pk}/payments/")
        assert response.status_code == 200
        assert len(response.json()) == 1

    def test_cloisonnement_entre_cliniques(
        self, clinic, other_clinic, accountant, other_patient
    ):
        make_invoice(other_clinic, other_patient)
        response = api_client(accountant).get("/api/v1/invoices/")
        assert response.status_code == 200
        assert response.json()["results"] == []
