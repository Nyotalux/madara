"""Dossiers patients : modèle, permissions, cloisonnement web et API."""

from __future__ import annotations

from datetime import date

import pytest
from django.core.exceptions import ValidationError
from django.test import Client
from django.urls import reverse
from rest_framework.test import APIClient

from apps.accounts.models import Membership
from apps.common.context import clinic_context
from apps.patients.models import Patient

from .factories import PASSWORD, UserFactory, make_staff

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Fabriques locales
# ---------------------------------------------------------------------------


DATE_FIELDS = ("birth_date", "insurance_expiry")


def make_patient(clinic, *, save=True, **kwargs):
    for field in DATE_FIELDS:
        if isinstance(kwargs.get(field), str):
            kwargs[field] = date.fromisoformat(kwargs[field])
    defaults = {
        "clinic": clinic,
        "first_name": "Salma",
        "last_name": "Bennis",
        "phone": "+212612345678",
    }
    defaults.update(kwargs)
    patient = Patient(**defaults)
    if save:
        patient.save()
    return patient


@pytest.fixture
def patient(clinic):
    return make_patient(clinic)


@pytest.fixture
def patients(clinic, other_clinic):
    return [
        make_patient(clinic, first_name="Amine", last_name="Zahra"),
        make_patient(clinic, first_name="Yasmine", last_name="Idrissi", city="Rabat"),
        make_patient(other_clinic, first_name="Rachid", last_name="Amrani"),
    ]


@pytest.fixture
def web():
    """Client Django connecté, avec la clinique mémorisée en session."""

    def _login(user, active_clinic=None):
        client = Client()
        assert client.login(username=user.email, password=PASSWORD)
        session = client.session
        session["madara_clinic_id"] = (active_clinic or user.primary_membership.clinic).pk
        session.save()
        return client

    return _login


@pytest.fixture
def api():
    return APIClient()


@pytest.fixture
def auth(api):
    def _authenticate(user):
        api.force_authenticate(user=user)
        return api

    return _authenticate


# ---------------------------------------------------------------------------
# Modèle
# ---------------------------------------------------------------------------


class TestPatientModel:
    def test_reference_attribuee_automatiquement(self, clinic):
        assert make_patient(clinic).reference == f"PT-{_year()}-00001"

    def test_references_incrementales_par_clinique(self, clinic, other_clinic):
        first = make_patient(clinic, first_name="A", last_name="Un")
        second = make_patient(clinic, first_name="B", last_name="Deux")
        elsewhere = make_patient(other_clinic, first_name="C", last_name="Trois")

        assert first.reference.endswith("00001")
        assert second.reference.endswith("00002")
        assert elsewhere.reference.endswith("00001")

    def test_telephone_normalise(self, clinic):
        assert make_patient(clinic, phone="+212 6 12 34 56 78").phone == "+212612345678"

    def test_nom_complet(self, clinic):
        assert make_patient(clinic).display_name == "Bennis Salma"

    def test_age_calcule(self, clinic):
        assert make_patient(clinic, birth_date="1990-05-12").age == _age("1990-05-12")

    def test_age_inconnu_sans_date_de_naissance(self, clinic):
        assert make_patient(clinic).age is None

    def test_date_de_naissance_future_refusee(self, clinic):
        with pytest.raises(ValidationError):
            make_patient(clinic, birth_date="2999-01-01")
        assert not Patient.objects.filter(birth_date="2999-01-01").exists()

    def test_adhesion_perimee(self, clinic):
        assert make_patient(clinic, insurance_expiry="2000-01-01").insurance_is_expired
        assert not make_patient(
            clinic, insurance_expiry="2999-01-01"
        ).insurance_is_expired

    def test_alertes_medicales(self, clinic):
        assert make_patient(clinic, allergies="Pénicilline").has_medical_flags
        assert make_patient(clinic, current_medication="Aspirine").has_medical_flags
        assert not make_patient(clinic).has_medical_flags

    def test_cin_normalise_en_majuscules(self, clinic):
        assert make_patient(clinic, cin="ab123456").cin == "AB123456"

    def test_cin_invalide_refuse(self, clinic):
        with pytest.raises(ValidationError):
            make_patient(clinic, cin="1234", save=False).full_clean()

    def test_recherche_insensible_a_la_casse(self, clinic):
        target = make_patient(clinic, first_name="Salma")
        assert target.matches_search("sALMa")
        assert target.matches_search(target.reference.lower())
        assert not target.matches_search("karim")

    def test_archivage_logique(self, clinic):
        target = make_patient(clinic)
        target.archive("dossier clos")

        assert target.is_active is False
        assert target.deletion_reason == "dossier clos"
        assert not Patient.objects.filter(pk=target.pk).exists()
        assert Patient.all_objects.filter(pk=target.pk).exists()

    def test_restauration(self, clinic):
        target = make_patient(clinic)
        target.archive("test")
        target.restore()

        assert Patient.objects.filter(pk=target.pk).exists()


def _year() -> int:
    return date.today().year


def _age(birth_date: str) -> int:
    born = date.fromisoformat(birth_date)
    today = date.today()
    return today.year - born.year - ((today.month, today.day) < (born.month, born.day))


# ---------------------------------------------------------------------------
# Cloisonnement
# ---------------------------------------------------------------------------


class TestTenantIsolation:
    def test_manager_filtre_sur_la_clinique_courante(
        self, patients, clinic, other_clinic
    ):
        with clinic_context(clinic):
            assert Patient.objects.count() == 2
        with clinic_context(other_clinic):
            assert Patient.objects.count() == 1
        assert Patient.all_objects.count() == 3

    def test_dossier_d_une_autre_clinique_invisible(self, web, clinic, other_clinic):
        user, _ = make_staff(clinic, Membership.Role.RECEPTION)
        patient = make_patient(other_clinic, first_name="Rachid")
        client = web(user)

        assert (
            client.get(reverse("web:patient-detail", args=[patient.pk])).status_code
            == 404
        )

    def test_liste_web_limitee_a_la_clinique(self, web, patients, clinic):
        user, _ = make_staff(clinic, Membership.Role.RECEPTION)
        response = web(user).get(reverse("web:patient-list"))

        assert response.status_code == 200
        assert b"Rachid" not in response.content
        assert b"Zahra" in response.content

    def test_creation_forsee_la_clinique_du_post(self, web, clinic, other_clinic):
        """Un ``clinic`` dans le POST ne permet pas d'écrire ailleurs."""
        user, _ = make_staff(clinic, Membership.Role.ADMIN)
        client = web(user)
        response = client.post(
            reverse("web:patient-create"),
            {
                "first_name": "Nadia",
                "last_name": "Tazi",
                "phone": "0612345678",
                "clinic": other_clinic.pk,
            },
        )
        assert response.status_code == 302

        created = Patient.all_objects.get(last_name="Tazi")
        assert created.clinic == clinic
        assert not Patient.all_objects.filter(clinic=other_clinic).exists()


# ---------------------------------------------------------------------------
# Permissions web
# ---------------------------------------------------------------------------


class TestWebPermissions:
    @pytest.mark.parametrize(
        "role",
        [
            Membership.Role.RECEPTION,
            Membership.Role.DOCTOR,
            Membership.Role.NURSE,
            Membership.Role.ADMIN,
        ],
    )
    def test_soignant_peut_creer_un_dossier(self, web, clinic, role):
        user, _ = make_staff(clinic, role)
        client = web(user)

        assert client.get(reverse("web:patient-create")).status_code == 200
        response = client.post(
            reverse("web:patient-create"),
            {"first_name": "Youssef", "last_name": "Alaoui"},
        )
        assert response.status_code == 302

    def test_comptable_exclu_des_dossiers(self, web, clinic, accountant):
        client = web(accountant)

        assert client.get(reverse("web:patient-list")).status_code == 403
        assert client.get(reverse("web:patient-create")).status_code == 403

    def test_comptable_ne_peut_pas_voir_un_dossier(
        self, web, clinic, accountant, patient
    ):
        assert (
            web(accountant)
            .get(reverse("web:patient-detail", args=[patient.pk]))
            .status_code
            == 403
        )

    def test_anonyme_redirige(self, client, patient):
        response = client.get(reverse("web:patient-list"))
        assert response.status_code == 302
        assert reverse("web:login") in response.url

    def test_archivage_reserve_a_l_administrateur(self, web, clinic, doctor, patient):
        client = web(doctor)
        assert (
            client.get(reverse("web:patient-archive", args=[patient.pk])).status_code
            == 403
        )

    def test_administrateur_archive_le_dossier(self, web, clinic, clinic_admin, patient):
        client = web(clinic_admin)
        assert (
            client.get(reverse("web:patient-archive", args=[patient.pk])).status_code
            == 200
        )

        response = client.post(
            reverse("web:patient-archive", args=[patient.pk]), {"reason": "dossier clos"}
        )
        assert response.status_code == 302
        patient.refresh_from_db()
        assert patient.is_active is False

    def test_restauration_par_l_administrateur(self, web, clinic, clinic_admin, patient):
        patient.archive("erreur")
        client = web(clinic_admin)
        client.post(reverse("web:patient-restore", args=[patient.pk]))

        patient.refresh_from_db()
        assert patient.is_active is True


# ---------------------------------------------------------------------------
# Recherche et filtres web
# ---------------------------------------------------------------------------


class TestListFilters:
    def test_recherche_par_nom(self, web, clinic, patients):
        user, _ = make_staff(clinic, Membership.Role.RECEPTION)
        response = web(user).get(reverse("web:patient-list"), {"q": "Zahra"})

        assert b"Zahra" in response.content
        assert b"Idrissi" not in response.content

    def test_recherche_par_telephone(self, web, clinic):
        user, _ = make_staff(clinic, Membership.Role.RECEPTION)
        make_patient(clinic, first_name="Ali", last_name="Berrada", phone="0661234567")

        response = web(user).get(reverse("web:patient-list"), {"q": "0661234567"})
        assert b"Berrada" in response.content

    def test_filtre_ville(self, web, clinic):
        user, _ = make_staff(clinic, Membership.Role.RECEPTION)
        make_patient(clinic, last_name="Casablanca1", city="Casablanca")
        make_patient(clinic, last_name="Rabat1", city="Rabat")

        response = web(user).get(reverse("web:patient-list"), {"city": "Rabat"})
        assert b"Rabat1" in response.content
        assert b"Casablanca1" not in response.content

    def test_filtre_statut(self, web, clinic):
        user, _ = make_staff(clinic, Membership.Role.RECEPTION)
        archived = make_patient(clinic, last_name="Archive1")
        archived.archive("clos")
        make_patient(clinic, last_name="Actif1")

        response = web(user).get(reverse("web:patient-list"), {"status": "archived"})
        assert b"Archive1" in response.content
        assert b"Actif1" not in response.content

    def test_compteurs_comprennent_les_archives(self, web, clinic, patients):
        user, _ = make_staff(clinic, Membership.Role.RECEPTION)
        archived = make_patient(clinic, last_name="Clos")
        archived.archive("clos")

        stats = web(user).get(reverse("web:patient-list")).context["stats"]
        assert stats["total"] == 3
        assert stats["active"] == 2
        assert stats["archived"] == 1

    def test_statistiques(self, web, clinic, patients):
        user, _ = make_staff(clinic, Membership.Role.RECEPTION)
        response = web(user).get(reverse("web:patient-list"))

        # Les 2 dossiers de la clinique, pas celui de l'autre clinique.
        assert response.context["stats"]["total"] == 2
        assert response.context["stats"]["active"] == 2
        assert response.context["stats"]["archived"] == 0

    def test_pagination(self, web, clinic):
        user, _ = make_staff(clinic, Membership.Role.RECEPTION)
        for index in range(30):
            make_patient(clinic, first_name=f"Patient{index:02d}", last_name="Lot")

        response = web(user).get(reverse("web:patient-list"))
        assert response.context["page_obj"].number == 1
        assert len(response.context["patients"]) == 25

        second = web(user).get(reverse("web:patient-list"), {"page": 2})
        assert len(second.context["patients"]) == 5

    def test_autocomplete_exige_deux_caracteres(self, web, clinic):
        user, _ = make_staff(clinic, Membership.Role.RECEPTION)
        make_patient(clinic, first_name="Salma")
        client = web(user)

        assert (
            b"Salma"
            not in client.get(reverse("web:patient-autocomplete"), {"q": "s"}).content
        )
        assert (
            b"Salma"
            in client.get(reverse("web:patient-autocomplete"), {"q": "salma"}).content
        )


# ---------------------------------------------------------------------------
# Formulaire
# ---------------------------------------------------------------------------


class TestPatientForm:
    def test_insured_exige_un_assureur(self, clinic):
        from apps.patients.forms import PatientForm

        form = PatientForm(
            {"first_name": "A", "last_name": "B", "is_insured": True},
            clinic=clinic,
        )
        assert not form.is_valid()
        assert "insurer" in form.errors

    def test_grossesse_incoherente(self, clinic):
        from apps.patients.forms import PatientForm

        form = PatientForm(
            {"first_name": "A", "last_name": "B", "gender": "M", "is_pregnant": True},
            clinic=clinic,
        )
        assert not form.is_valid()
        assert "is_pregnant" in form.errors

    def test_formulaire_valide(self, clinic):
        from apps.patients.forms import PatientForm

        form = PatientForm(
            {
                "first_name": "A",
                "last_name": "B",
                "is_insured": True,
                "insurer": "CNSS",
                "cin": "ab123456",
            },
            clinic=clinic,
        )
        assert form.is_valid(), form.errors
        assert form.cleaned_data["cin"] == "AB123456"


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------


class TestPatientApi:
    def test_liste_cloisonnee(self, auth, clinic, other_clinic, patients):
        user, _ = make_staff(clinic, Membership.Role.RECEPTION)
        response = auth(user).get(reverse("patient-list"))

        assert response.status_code == 200
        assert response.json()["count"] == 2

    def test_recherche_api(self, auth, clinic, patients):
        user, _ = make_staff(clinic, Membership.Role.RECEPTION)
        response = auth(user).get(reverse("patient-list"), {"q": "Zahra"})

        assert response.json()["count"] == 1
        assert response.json()["results"][0]["last_name"] == "Zahra"

    def test_recherche_rapide(self, auth, clinic, patients):
        user, _ = make_staff(clinic, Membership.Role.RECEPTION)
        response = auth(user).get(reverse("patient-search"), {"q": "Yas"})

        assert response.status_code == 200
        assert response.json()[0]["last_name"] == "Idrissi"

    def test_recherche_rapide_trop_courte(self, auth, clinic, patients):
        user, _ = make_staff(clinic, Membership.Role.RECEPTION)
        assert auth(user).get(reverse("patient-search"), {"q": "Y"}).json() == []

    def test_creation_par_l_api(self, auth, clinic):
        user, _ = make_staff(clinic, Membership.Role.NURSE)
        response = auth(user).post(
            reverse("patient-list"),
            {"first_name": "Hind", "last_name": "Lamrani", "phone": "0611223344"},
            format="json",
        )
        assert response.status_code == 201
        assert response.json()["reference"].startswith("PT-")

        created = Patient.all_objects.get(last_name="Lamrani")
        assert created.clinic == clinic
        assert created.created_by == user

    def test_creation_par_la_comptable_refusee(self, auth, clinic, accountant):
        response = auth(accountant).post(
            reverse("patient-list"),
            {"first_name": "X", "last_name": "Y"},
            format="json",
        )
        assert response.status_code == 403

    def test_lecture_par_la_comptable_refusee(self, auth, clinic, accountant, patient):
        assert auth(accountant).get(reverse("patient-list")).status_code == 403
        assert (
            auth(accountant).get(reverse("patient-detail", args=[patient.pk])).status_code
            == 403
        )

    def test_detail_d_une_autre_clinique_404(self, auth, clinic, other_clinic):
        user, _ = make_staff(clinic, Membership.Role.DOCTOR)
        patient = make_patient(other_clinic)
        assert (
            auth(user).get(reverse("patient-detail", args=[patient.pk])).status_code
            == 404
        )

    def test_modification_autorisee_au_medecin(self, auth, clinic, doctor, patient):
        response = auth(doctor).patch(
            reverse("patient-detail", args=[patient.pk]),
            {"city": "Casablanca"},
            format="json",
        )
        assert response.status_code == 200
        patient.refresh_from_db()
        assert patient.city == "Casablanca"

    def test_suppression_refusee(self, auth, clinic, clinic_admin, patient):
        """L'historique médical est conservé : on archive, on ne supprime pas."""
        response = auth(clinic_admin).delete(reverse("patient-detail", args=[patient.pk]))
        assert response.status_code == 405
        assert Patient.all_objects.filter(pk=patient.pk, is_active=True).exists()

    def test_archivage_par_administrateur(self, auth, clinic, clinic_admin, patient):
        response = auth(clinic_admin).post(
            reverse("patient-archive", args=[patient.pk]),
            {"reason": "dossier soldé"},
            format="json",
        )
        assert response.status_code == 200
        patient.refresh_from_db()
        assert patient.is_active is False

    def test_archivage_refuse_au_medecin(self, auth, clinic, doctor, patient):
        response = auth(doctor).post(
            reverse("patient-archive", args=[patient.pk]), {}, format="json"
        )
        assert response.status_code == 403
        patient.refresh_from_db()
        assert patient.is_active is True

    def test_archivage_refuse_a_l_accueil(self, auth, clinic, receptionist, patient):
        response = auth(receptionist).post(
            reverse("patient-archive", args=[patient.pk]), {}, format="json"
        )
        assert response.status_code == 403

    def test_les_archives_sont_masquees_par_defaut(self, auth, clinic, patient):
        user, _ = make_staff(clinic, Membership.Role.ADMIN)
        patient.archive("clos")

        assert auth(user).get(reverse("patient-list")).json()["count"] == 0
        assert (
            auth(user)
            .get(reverse("patient-list"), {"include_archived": 1})
            .json()["count"]
            == 1
        )

    def test_compteurs(self, auth, clinic, patients):
        user, _ = make_staff(clinic, Membership.Role.ADMIN)
        payload = auth(user).get(reverse("patient-statistics")).json()

        assert payload["visible"] == 2

    def test_naissance_future_refusee(self, auth, clinic):
        user, _ = make_staff(clinic, Membership.Role.DOCTOR)
        response = auth(user).post(
            reverse("patient-list"),
            {"first_name": "A", "last_name": "B", "birth_date": "2999-01-01"},
            format="json",
        )
        assert response.status_code == 400

    def test_compte_sans_clinique_refuse(self, api, db):
        user = UserFactory.create(email="sans-clinique@example.ma")
        api.force_authenticate(user=user)
        assert api.get(reverse("patient-list")).status_code == 403

    def test_anonyme_refuse(self, api):
        assert api.get(reverse("patient-list")).status_code == 401

    def test_administrateur_plateforme_voit_la_clinique_choisie(
        self, auth, platform_admin, patients, clinic
    ):
        response = auth(platform_admin).get(
            reverse("patient-list"), HTTP_X_CLINIC=clinic.slug
        )
        assert response.json()["count"] == 2
