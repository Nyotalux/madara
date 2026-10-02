"""Authentification, permissions et cloisonnement de l'API mobile."""

from __future__ import annotations

import pytest
from django.urls import reverse
from rest_framework.test import APIClient

from apps.accounts.models import Membership, OpeningHour

from .factories import PASSWORD, make_staff

pytestmark = pytest.mark.django_db


@pytest.fixture
def api(client):
    return APIClient()


@pytest.fixture
def jwt_api(api, doctor, clinic):
    api.force_authenticate(user=doctor)
    return api


@pytest.fixture
def auth(api, user=None):
    def _authenticate(target):
        api.force_authenticate(user=target)
        return api

    return _authenticate


# ---------------------------------------------------------------------------
# Authentification
# ---------------------------------------------------------------------------


class TestTokenLogin:
    def test_login_valide_renvoie_les_jetons_et_le_contexte(self, api, doctor, clinic):
        response = api.post(
            reverse("login"), {"email": doctor.email, "password": PASSWORD}, format="json"
        )
        assert response.status_code == 200
        payload = response.json()
        assert payload["access"] and payload["refresh"]
        assert payload["user"]["email"] == doctor.email
        assert payload["default_clinic_id"] == clinic.pk
        assert [item["clinic_name"] for item in payload["memberships"]] == [clinic.name]

    def test_login_refuse_un_mot_de_passe_errone(self, api, doctor):
        response = api.post(
            reverse("login"), {"email": doctor.email, "password": "faux"}, format="json"
        )
        assert response.status_code == 401

    def test_refresh_prolonge_la_session(self, api, doctor):
        login = api.post(
            reverse("login"), {"email": doctor.email, "password": PASSWORD}, format="json"
        ).json()
        api.credentials(HTTP_AUTHORIZATION="")
        api.force_authenticate(user=None)
        response = api.post(
            reverse("refresh"), {"refresh": login["refresh"]}, format="json"
        )
        assert response.status_code == 200
        assert "access" in response.json()

    def test_logout_demande_un_jeton_de_rafraichissement(self, api, doctor):
        api.force_authenticate(user=doctor)
        assert api.post(reverse("logout"), {}, format="json").status_code == 400
        assert (
            api.post(reverse("logout"), {"refresh": "x"}, format="json").status_code
            == 200
        )


class TestAccessControl:
    @pytest.mark.parametrize(
        "url_name",
        ["me", "clinic-list", "clinic-current", "staff-list", "membership-list"],
    )
    def test_anonyme_refuse(self, api, url_name):
        assert api.get(reverse(url_name)).status_code == 401

    def test_compte_sans_clinique_neVoit_aucune_clinique(
        self, api, auth, user_without_clinic
    ):
        auth(user_without_clinic)
        assert api.get(reverse("clinic-list")).json()["count"] == 0

    def test_compte_sans_clinique_refuse_les_donnees_operationnelles(
        self, api, auth, user_without_clinic
    ):
        auth(user_without_clinic)
        for url_name in ("staff-list", "membership-list"):
            assert api.get(reverse(url_name)).status_code == 403
        assert api.get(reverse("clinic-current")).status_code == 404

    @pytest.fixture
    def user_without_clinic(self, db):
        from .factories import UserFactory

        return UserFactory.create(email="sans-clinique@example.ma")


class TestMeEndpoint:
    def test_profil_avec_clinique_et_role(self, jwt_api, clinic):
        payload = jwt_api.get(reverse("me")).json()
        assert payload["email"]
        assert payload["current_clinic"]["slug"] == clinic.slug
        assert payload["role"] == Membership.Role.DOCTOR

    def test_mise_a_jour_du_prenom(self, jwt_api):
        response = jwt_api.patch(reverse("me"), {"first_name": "Salmaa"}, format="json")
        assert response.status_code == 200
        assert response.json()["first_name"] == "Salmaa"

    def test_email_invalide_refuse(self, jwt_api):
        response = jwt_api.patch(reverse("me"), {"email": "pas-un-email"}, format="json")
        assert response.status_code == 400


# ---------------------------------------------------------------------------
# Choix de la clinique courante
# ---------------------------------------------------------------------------


class TestClinicContext:
    def test_clinique_courante_sans_entete_est_la_clinique_par_defaut(
        self, jwt_api, clinic
    ):
        payload = jwt_api.get(reverse("clinic-current")).json()
        assert payload["slug"] == clinic.slug

    def test_entete_x_clinic_selectionne_une_autre_clinique(
        self, auth, clinic, other_clinic
    ):
        from .factories import MembershipFactory

        doctor = MembershipFactory.create(clinic=clinic, role=Membership.Role.DOCTOR).user
        MembershipFactory.create(
            user=doctor, clinic=other_clinic, role=Membership.Role.NURSE
        )
        response = auth(doctor).get(
            reverse("clinic-current"), HTTP_X_CLINIC=other_clinic.slug
        )
        assert response.json()["slug"] == other_clinic.slug

    def test_entete_x_clinic_hors_perimetre_renvoie_404(self, jwt_api, other_clinic):
        response = jwt_api.get(reverse("clinic-current"), HTTP_X_CLINIC=other_clinic.slug)
        assert response.status_code == 404

    def test_clinique_inconnue_renvoie_404(self, jwt_api):
        assert (
            jwt_api.get(reverse("clinic-current"), HTTP_X_CLINIC="fantome").status_code
            == 404
        )

    def test_switch_clinic_refuse_une_clinique_non_autorisee(self, jwt_api, other_clinic):
        response = jwt_api.post(
            reverse("switch-clinic"), {"clinic": other_clinic.pk}, format="json"
        )
        assert response.status_code == 403

    def test_switch_clinic_accepte_une_clinique_autorisee(
        self, auth, clinic, other_clinic
    ):
        from .factories import MembershipFactory

        doctor = MembershipFactory.create(clinic=clinic, role=Membership.Role.DOCTOR).user
        MembershipFactory.create(
            user=doctor, clinic=other_clinic, role=Membership.Role.DOCTOR
        )
        response = auth(doctor).post(
            reverse("switch-clinic"), {"clinic": other_clinic.pk}, format="json"
        )
        assert response.status_code == 200

    def test_administrateur_plateforme_bascule_vers_toute_clinique(
        self, auth, platform_admin, clinic, other_clinic
    ):
        auth(platform_admin)
        response = auth(platform_admin).get(
            reverse("clinic-current"), HTTP_X_CLINIC=other_clinic.slug
        )
        assert response.json()["slug"] == other_clinic.slug

    def test_clinique_desactivee_disparait_du_contexte(self, jwt_api, clinic):
        clinic.is_active = False
        clinic.save()
        assert jwt_api.get(reverse("clinic-list")).json()["count"] == 0
        assert jwt_api.get(reverse("clinic-current")).status_code == 404


# ---------------------------------------------------------------------------
# Cloisonnement des listes
# ---------------------------------------------------------------------------


class TestTenantIsolation:
    def test_annuaire_du_personnel_est_limite_a_la_clinique(
        self, auth, clinic, other_clinic
    ):
        colleague_user, _ = make_staff(clinic, Membership.Role.NURSE, last_name="Amal")
        colleague = make_staff(clinic, Membership.Role.ACCOUNTANT, last_name="Hamza")[1]
        stranger = make_staff(other_clinic, Membership.Role.NURSE, last_name="Rachid")[1]
        api = auth(colleague_user)

        payload = api.get(reverse("staff-list")).json()
        assert payload["count"] == 2
        assert {item["id"] for item in payload["results"]} == {
            colleague_user.membership_set.get(clinic=clinic).pk,
            colleague.pk,
        }
        assert stranger.pk not in {item["id"] for item in payload["results"]}

    def test_horaires_ne_montrent_que_la_clinique_courante(
        self, auth, clinic, other_clinic
    ):
        from .factories import MembershipFactory

        doctor = MembershipFactory.create(clinic=clinic, role=Membership.Role.DOCTOR)
        stranger = MembershipFactory.create(
            clinic=other_clinic, role=Membership.Role.DOCTOR
        )
        OpeningHour.objects.create(
            membership=doctor, weekday=1, start_time="09:00", end_time="12:00"
        )
        OpeningHour.objects.create(
            membership=stranger, weekday=2, start_time="09:00", end_time="12:00"
        )
        api = auth(doctor.user)

        # ``list`` renvoie la liste brute des horaires du praticien connecté.
        assert len(api.get(reverse("opening-hour-list")).json()) == 1

    def test_detail_d_un_objet_d_une_autre_clinique_renvoie_404(
        self, auth, clinic, other_clinic
    ):
        _, own = make_staff(clinic, Membership.Role.NURSE)
        _, stranger = make_staff(other_clinic, Membership.Role.NURSE)
        api = auth(own.user)

        assert api.get(reverse("staff-detail", args=[own.pk])).status_code == 200
        assert api.get(reverse("staff-detail", args=[stranger.pk])).status_code == 404

    def test_administrateur_plateforme_voit_les_deux_cliniques(
        self, auth, platform_admin, clinic, other_clinic
    ):
        make_staff(clinic, Membership.Role.NURSE)
        make_staff(other_clinic, Membership.Role.NURSE)
        api = auth(platform_admin)

        assert api.get(reverse("clinic-list")).json()["count"] == 2
