"""Paie : fiches de salaire, bulletins, primes, web et API (jalon 6)."""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.urls import reverse
from rest_framework.test import APIClient

from apps.accounts.models import Membership
from apps.payroll import services
from apps.payroll.models import Bonus, Payslip

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_staff(clinic_role_tuple):
    from tests.factories import make_staff as factory_staff

    clinic, role = clinic_role_tuple
    return factory_staff(clinic, role)


def api_client(user) -> APIClient:
    api = APIClient()
    api.force_authenticate(user=user)
    return api


def _membership(clinic, user) -> Membership:
    return user.memberships.get(clinic=clinic)


# ---------------------------------------------------------------------------
# Fixtures (salaire de base fixé sur le membership)
# ---------------------------------------------------------------------------


@pytest.fixture
def clinic_admin_membership(clinic, clinic_admin):
    membership = _membership(clinic, clinic_admin)
    membership.base_salary = Decimal("9000.00")
    membership.save(update_fields=["base_salary"])
    return membership


@pytest.fixture
def accountant_membership(clinic, accountant):
    membership = _membership(clinic, accountant)
    membership.base_salary = Decimal("6000.00")
    membership.save(update_fields=["base_salary"])
    return membership


@pytest.fixture
def doctor_membership(clinic, doctor):
    membership = _membership(clinic, doctor)
    membership.base_salary = Decimal("9000.00")
    membership.save(update_fields=["base_salary"])
    return membership


# ---------------------------------------------------------------------------
# Primes, périodes et bulletins
# ---------------------------------------------------------------------------


class TestBonuses:
    def test_prime_valide_pour_la_periode_courante(self, clinic, doctor_membership):
        bonus = services.add_bonus(
            clinic,
            doctor_membership,
            kind=Bonus.Kind.BONUS,
            label="Garde de nuit",
            amount=Decimal("500.00"),
        )
        assert bonus.period == services.current_period()
        assert bonus.membership.clinic_id == clinic.pk

    def test_prime_pour_une_periode_precise(self, clinic, doctor_membership):
        bonus = services.add_bonus(
            clinic,
            doctor_membership,
            kind=Bonus.Kind.BONUS,
            label="Rétroactif",
            amount=Decimal("250.00"),
            period="2026-08",
        )
        assert bonus.period == "2026-08"

    def test_mode_de_periode_invalide_refuse(self, clinic, doctor_membership):
        with pytest.raises(ValidationError):
            services.add_bonus(
                clinic,
                doctor_membership,
                kind=Bonus.Kind.BONUS,
                label="Avance",
                amount=Decimal("100.00"),
                period="octobre",
            )

    def test_montant_non_positif_refuse(self, clinic, doctor_membership):
        with pytest.raises(ValidationError):
            services.add_bonus(
                clinic,
                doctor_membership,
                kind=Bonus.Kind.BONUS,
                label="Avance",
                amount=Decimal("0"),
            )

    def test_retenue_signe_est_negatif(self, clinic, doctor_membership):
        bonus = services.add_bonus(
            clinic,
            doctor_membership,
            kind=Bonus.Kind.DEDUCTION,
            label="Avance sur salaire",
            amount=Decimal("300.00"),
        )
        assert bonus.signed_amount == Decimal("-300.00")

    def test_retrait_tant_que_le_bulletin_n_est_pas_paye(self, clinic, doctor_membership):
        bonus = services.add_bonus(
            clinic,
            doctor_membership,
            kind=Bonus.Kind.BONUS,
            label="Prime",
            amount=Decimal("100.00"),
        )
        services.remove_bonus(bonus)
        assert Bonus.objects.count() == 0

    def test_retrait_refuse_apres_paiement_du_bulletin(self, clinic, doctor_membership):
        bonus = services.add_bonus(
            clinic,
            doctor_membership,
            kind=Bonus.Kind.BONUS,
            label="Prime",
            amount=Decimal("100.00"),
        )
        services.generate_payslips(clinic, period=bonus.period)
        payslip = Payslip.objects.get(clinic=clinic, period=bonus.period)
        services.pay_payslip(payslip)
        with pytest.raises(ValidationError):
            services.remove_bonus(bonus)


class TestPayslips:
    def test_generation_reprend_salaires_primes_retenues(
        self, clinic, doctor_membership, accountant_membership, clinic_admin_membership
    ):
        services.add_bonus(
            clinic,
            doctor_membership,
            kind=Bonus.Kind.BONUS,
            label="Garde",
            amount=Decimal("500.00"),
            period="2026-09",
        )
        services.add_bonus(
            clinic,
            doctor_membership,
            kind=Bonus.Kind.DEDUCTION,
            label="Avance",
            amount=Decimal("200.00"),
            period="2026-09",
        )
        created = services.generate_payslips(clinic, period="2026-09")
        assert len(created) == 3  # médecin, comptable, administrateur actifs
        payslip = Payslip.objects.get(clinic=clinic, membership=doctor_membership)
        assert payslip.period == "2026-09"
        assert payslip.base_amount == Decimal("9000.00")
        assert payslip.bonus_amount == Decimal("500.00")
        assert payslip.deduction_amount == Decimal("200.00")
        assert payslip.net_amount == Decimal("9300.00")
        assert payslip.status == Payslip.Status.DRAFT
        assert payslip.reference.startswith("BS-")

    def test_un_seul_bulletin_par_membre_et_par_periode(self, clinic, doctor_membership):
        services.generate_payslips(clinic, period="2026-09")
        again = services.generate_payslips(clinic, period="2026-09")
        assert again == []
        assert Payslip.objects.filter(clinic=clinic, period="2026-09").count() == 1

    def test_paiement_est_definitif(self, clinic, doctor_membership):
        services.generate_payslips(clinic, period="2026-09")
        payslip = Payslip.objects.get(clinic=clinic, period="2026-09")
        services.pay_payslip(payslip)
        payslip.refresh_from_db()
        assert payslip.status == Payslip.Status.PAID
        assert payslip.paid_at is not None
        with pytest.raises(ValidationError):
            services.update_payslip(payslip, bonus_amount=100)
        with pytest.raises(ValidationError):
            services.cancel_payslip(payslip)

    def test_annulation_d_un_brouillon(self, clinic, doctor_membership):
        services.generate_payslips(clinic, period="2026-09")
        payslip = Payslip.objects.get(clinic=clinic, period="2026-09")
        services.cancel_payslip(payslip, reason="erreur de traitement")
        payslip.refresh_from_db()
        assert payslip.status == Payslip.Status.CANCELLED
        assert "erreur" in payslip.cancellation_reason

    def test_ajustement_du_brouillon_seulement(self, clinic, doctor_membership):
        services.generate_payslips(clinic, period="2026-09")
        payslip = Payslip.objects.get(clinic=clinic, period="2026-09")
        services.update_payslip(
            payslip, bonus_amount=Decimal("150.00"), notes="heures sup"
        )
        payslip.refresh_from_db()
        assert payslip.bonus_amount == Decimal("150.00")
        assert payslip.net_amount == Decimal("9150.00")

    def test_montants_negatifs_refuses(self, clinic, doctor_membership):
        services.generate_payslips(clinic, period="2026-09")
        payslip = Payslip.objects.get(clinic=clinic, period="2026-09")
        with pytest.raises(ValidationError):
            services.update_payslip(payslip, bonus_amount=Decimal("-1"))

    def test_resume_de_periode(self, clinic, doctor_membership):
        services.generate_payslips(clinic, period="2026-09")
        payslip = Payslip.objects.get(clinic=clinic, period="2026-09")
        services.pay_payslip(payslip)
        summary = services.period_summary(clinic, "2026-09")
        assert summary["total"] == 1
        assert summary["drafts"] == 0
        assert summary["paid"] == 1
        assert summary["paid_total"] == payslip.net_amount

    def test_bulletins_limites_a_la_clinique(self, clinic, other_clinic):
        from tests.factories import make_staff as factory_staff

        other_staff = factory_staff(other_clinic, Membership.Role.ACCOUNTANT)[1]
        other_staff.base_salary = Decimal("5000.00")
        other_staff.save(update_fields=["base_salary"])
        services.generate_payslips(other_clinic, period="2026-09")
        assert Payslip.objects.filter(clinic=clinic, period="2026-09").count() == 0
        assert Payslip.objects.filter(clinic=other_clinic, period="2026-09").count() == 1


# ---------------------------------------------------------------------------
# Web
# ---------------------------------------------------------------------------


class TestWeb:
    def test_la_paie_est_reservee_a_admin_et_comptabilite(
        self, client, clinic, receptionist
    ):
        client.force_login(receptionist)
        response = client.get(reverse("web:payroll-home"))
        assert response.status_code == 403

    def test_liste_des_bulletins_et_une_periode(self, client, clinic, accountant):
        services.generate_payslips(clinic, period="2026-09")
        client.force_login(accountant)
        assert client.get(reverse("web:payroll-home")).status_code == 200
        response = client.get(reverse("web:period-detail", args=["2026-09"]))
        assert response.status_code == 200
        assert "BS-" in response.content.decode()

    def test_generation_identifie_nouveau_bulletin(
        self, client, clinic, accountant, accountant_membership
    ):
        client.force_login(accountant)
        response = client.post(reverse("web:period-generate", args=["2026-09"]))
        assert response.status_code == 302
        assert Payslip.objects.filter(clinic=clinic, period="2026-09").exists()

    def test_paiement_et_annulation_web(self, client, clinic, accountant):
        services.generate_payslips(clinic, period="2026-09")
        payslip = Payslip.objects.get(clinic=clinic, period="2026-09")
        client.force_login(accountant)

        response = client.post(reverse("web:payslip-pay", args=[payslip.pk]))
        payslip.refresh_from_db()
        assert response.status_code == 302 and payslip.status == Payslip.Status.PAID

    def test_ajout_de_prime_via_web(self, client, clinic, accountant, doctor_membership):
        client.force_login(accountant)
        response = client.post(
            reverse("web:bonus-create"),
            {
                "membership": doctor_membership.pk,
                "period": "2026-09",
                "kind": Bonus.Kind.BONUS,
                "label": "Garde de nuit",
                "amount": "500.00",
            },
        )
        assert response.status_code == 302
        assert Bonus.objects.filter(clinic=clinic).count() == 1


# ---------------------------------------------------------------------------
# API mobile
# ---------------------------------------------------------------------------


class TestApi:
    def test_les_soignants_sont_exclus(self, clinic, doctor):
        response = api_client(doctor).get("/api/v1/payslips/")
        assert response.status_code == 403

    def test_liste_des_bulletins(self, clinic, accountant):
        services.generate_payslips(clinic, period="2026-09")
        response = api_client(accountant).get("/api/v1/payslips/")
        assert response.status_code == 200
        assert len(response.json()["results"]) == 1

    def test_generation_via_api(self, clinic, accountant):
        response = api_client(accountant).post(
            "/api/v1/payslips/generate/", {"period": "2026-09"}, format="json"
        )
        assert response.status_code == 200, response.content
        assert response.json()["created"] == 1

    def test_paiement_via_api(self, clinic, accountant):
        services.generate_payslips(clinic, period="2026-09")
        payslip = Payslip.objects.get(clinic=clinic, period="2026-09")
        response = api_client(accountant).post(f"/api/v1/payslips/{payslip.pk}/pay/")
        assert response.status_code == 200
        payslip.refresh_from_db()
        assert payslip.status == Payslip.Status.PAID

    def test_creation_de_prime_via_api(self, clinic, accountant, doctor_membership):
        response = api_client(accountant).post(
            "/api/v1/bonuses/",
            {
                "membership": doctor_membership.pk,
                "period": "2026-09",
                "kind": "BONUS",
                "label": "Garde",
                "amount": "500.00",
            },
            format="json",
        )
        assert response.status_code == 201, response.content
        assert Bonus.objects.count() == 1

    def test_suppression_de_bulletin_refusee(self, clinic, accountant):
        services.generate_payslips(clinic, period="2026-09")
        payslip = Payslip.objects.get(clinic=clinic, period="2026-09")
        response = api_client(accountant).delete(f"/api/v1/payslips/{payslip.pk}/")
        assert response.status_code in (400, 403, 405)
        assert Payslip.objects.filter(pk=payslip.pk).exists()

    def test_cloisonnement_entre_cliniques(self, clinic, accountant, other_clinic):
        from tests.factories import make_staff as factory_staff

        staff = factory_staff(other_clinic, Membership.Role.ACCOUNTANT)[1]
        staff.base_salary = Decimal("5000.00")
        staff.save(update_fields=["base_salary"])
        services.generate_payslips(other_clinic, period="2026-09")
        response = api_client(accountant).get("/api/v1/payslips/")
        assert response.status_code == 200
        assert response.json()["results"] == []
