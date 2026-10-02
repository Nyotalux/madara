"""Back-office web : session, cloisonnement et permissions par rôle."""

from __future__ import annotations

import pytest
from django.urls import reverse

from apps.accounts.models import Membership
from apps.common.clinic import CLINIC_SESSION_KEY

from .factories import PASSWORD, MembershipFactory, make_staff

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Connexion
# ---------------------------------------------------------------------------


class TestLogin:
    def test_page_de_connexion_est_publique(self, client):
        response = client.get(reverse("web:login"))
        assert response.status_code == 200

    def test_pages_privees_redirigent_vers_la_connexion(self, client, clinic):
        for url_name in ("web:dashboard", "web:profile", "web:staff-list"):
            response = client.get(reverse(url_name))
            assert response.status_code == 302
            assert reverse("web:login") in response.url

    def test_connexion_valide_memorise_la_clinique(self, client, doctor, clinic):
        response = client.post(
            reverse("web:login"),
            {"username": doctor.email, "password": PASSWORD},
            follow=True,
        )
        assert response.status_code == 200
        assert client.session[CLINIC_SESSION_KEY] == clinic.pk

    def test_connexion_refuse_un_mot_de_passe_errone(self, client, doctor):
        response = client.post(
            reverse("web:login"), {"username": doctor.email, "password": "faux"}
        )
        assert response.status_code == 200
        assert "_auth_user_id" not in client.session

    def test_connexion_avec_clinique_demandee(self, client, clinic, other_clinic):
        from .factories import MembershipFactory

        doctor = MembershipFactory.create(clinic=clinic, role=Membership.Role.DOCTOR).user
        MembershipFactory.create(
            user=doctor, clinic=other_clinic, role=Membership.Role.DOCTOR
        )
        client.post(
            reverse("web:login"),
            {
                "username": doctor.email,
                "password": PASSWORD,
                "clinic": other_clinic.pk,
            },
        )
        assert client.session[CLINIC_SESSION_KEY] == other_clinic.pk

    def test_clinique_demandee_hors_perimetre_ignoree(self, client, clinic, other_clinic):
        doctor, _ = make_staff(clinic, Membership.Role.DOCTOR)
        client.post(
            reverse("web:login"),
            {
                "username": doctor.email,
                "password": PASSWORD,
                "clinic": other_clinic.pk,
            },
        )
        # Aucune clinique mémorisée : la clinique par défaut reste active.
        assert CLINIC_SESSION_KEY not in client.session
        response = client.get(reverse("web:dashboard"))
        assert response.context["clinic"] == clinic

    def test_deconnexion(self, client, logged_doctor):
        response = client.post(reverse("web:logout"))
        assert response.status_code == 302
        assert "_auth_user_id" not in client.session


@pytest.fixture
def auth(db, clinic):
    """Client Django connecté avec le mot de passe de test."""
    from django.test import Client

    def _login(user, active_clinic=None):
        client = Client()
        assert client.login(username=user.email, password=PASSWORD)
        membership = user.primary_membership
        if membership is not None:
            session = client.session
            session["madara_clinic_id"] = (active_clinic or membership.clinic).pk
            session.save()
        return client

    return _login


# ---------------------------------------------------------------------------
# Cloisonnement web
# ---------------------------------------------------------------------------


class TestClinicSwitch:
    def test_bascule_vers_une_clinique_autorisee(self, auth, clinic, other_clinic):
        doctor = MembershipFactory.create(clinic=clinic, role=Membership.Role.DOCTOR).user
        MembershipFactory.create(
            user=doctor, clinic=other_clinic, role=Membership.Role.DOCTOR
        )
        client = auth(doctor)

        response = client.post(reverse("web:switch-clinic"), {"clinic": other_clinic.pk})
        assert response.status_code == 302
        assert client.session["madara_clinic_id"] == other_clinic.pk

    def test_bascule_refusee_vers_une_clinique_non_autorisee(
        self, auth, clinic, other_clinic
    ):
        user, _ = make_staff(clinic, Membership.Role.DOCTOR)
        client = auth(user)

        response = client.post(reverse("web:switch-clinic"), {"clinic": other_clinic.pk})
        assert response.status_code == 404
        assert client.session[CLINIC_SESSION_KEY] == clinic.pk

    def test_personnel_visible_est_limite_a_la_clinique_courante(
        self, auth, clinic, other_clinic
    ):
        user, _ = make_staff(clinic, Membership.Role.DOCTOR)
        make_staff(clinic, Membership.Role.NURSE, last_name="Amal")
        make_staff(other_clinic, Membership.Role.NURSE, last_name="Rachid")
        client = auth(user)

        response = client.get(reverse("web:staff-list"))
        assert response.status_code == 200
        assert b"Amal" in response.content
        assert b"Rachid" not in response.content

    def test_fiche_du_personnel_d_une_autre_clinique_renvoie_404(
        self, auth, clinic, other_clinic
    ):
        user, _ = make_staff(clinic, Membership.Role.DOCTOR)
        _, stranger = make_staff(other_clinic, Membership.Role.NURSE)
        client = auth(user)

        assert (
            client.get(reverse("web:staff-detail", args=[stranger.pk])).status_code == 404
        )


class TestStaffView:
    def test_medecin_ne_peut_pas_creer_de_membre(self, auth, doctor):
        client = auth(doctor)
        assert client.get(reverse("web:staff-create")).status_code == 403
        assert client.post(reverse("web:staff-create"), {}).status_code == 403

    def test_accueil_ne_peut_pas_creer_de_membre(self, auth, receptionist):
        client = auth(receptionist)
        assert client.get(reverse("web:staff-create")).status_code == 403

    def test_administrateur_peut_ouvrir_le_formulaire(self, auth, clinic_admin):
        client = auth(clinic_admin)
        assert client.get(reverse("web:staff-create")).status_code == 200

    def test_administrateur_cree_un_membre_avec_role(self, auth, clinic, clinic_admin):
        client = auth(clinic_admin)
        response = client.post(
            reverse("web:staff-create"),
            {
                "email": "nouveau.medecin@example.ma",
                "first_name": "Nadia",
                "last_name": "Tazi",
                "role": Membership.Role.DOCTOR,
                "employee_number": "DR-777",
                "password": "MotDePasse2026!",
            },
        )
        assert response.status_code == 302
        membership = Membership.all_objects.get(
            clinic=clinic, role=Membership.Role.DOCTOR, employee_number="DR-777"
        )
        assert membership.user.email == "nouveau.medecin@example.ma"
        assert membership.user.check_password("MotDePasse2026!")

    def test_administrateur_ne_peut_pas_creer_un_membre_dans_une_autre_clinique(
        self, auth, clinic, other_clinic, clinic_admin
    ):
        """Le clinic_id du POST est ignoré : la clinique vient de la session."""
        client = auth(clinic_admin)
        client.post(
            reverse("web:staff-create"),
            {
                "email": "injection@example.ma",
                "last_name": "Pirate",
                "role": Membership.Role.DOCTOR,
                "clinic": other_clinic.pk,
            },
        )
        assert not Membership.all_objects.filter(
            clinic=other_clinic, user__email="injection@example.ma"
        ).exists()
        assert Membership.all_objects.filter(
            clinic=clinic, user__email="injection@example.ma"
        ).exists()

    def test_compte_sans_clinique_est_redirige_vers_la_connexion(self, client, db):
        from .factories import UserFactory

        client.force_login(UserFactory.create(email="sans-clinique@example.ma"))
        response = client.get(reverse("web:staff-create"))
        assert response.status_code == 302
        assert reverse("web:login") in response.url


class TestProfile:
    def test_mise_a_jour_du_profil(self, auth, doctor):
        client = auth(doctor)
        response = client.post(
            reverse("web:profile"),
            {
                "first_name": "Salmaa",
                "last_name": "El Fassi",
                "email": doctor.email,
                "phone": "+212612345678",
                "locale": "fr",
            },
            follow=True,
        )
        assert response.status_code == 200
        doctor.refresh_from_db()
        assert doctor.first_name == "Salmaa"
        assert doctor.phone == "+212612345678"

    def test_changement_de_mot_de_passe(self, auth, doctor):
        client = auth(doctor)
        response = client.post(
            reverse("web:change-password"),
            {
                "old_password": PASSWORD,
                "new_password1": "NouveauMotDePasse2026!",
                "new_password2": "NouveauMotDePasse2026!",
            },
        )
        assert response.status_code == 302
        doctor.refresh_from_db()
        assert doctor.check_password("NouveauMotDePasse2026!")


class TestHealthEndpoints:
    def test_health(self, client):
        assert client.get("/health/").json()["status"] == "ok"

    def test_health_db(self, client):
        payload = client.get("/health/db/").json()
        assert payload["status"] == "ok"
        assert payload["checks"]["database"]["ok"] is True

    def test_health_ready(self, client):
        payload = client.get("/health/ready/").json()
        assert payload["checks"]["database"]["ok"] is True
        assert payload["checks"]["broker"]["ok"] is True
