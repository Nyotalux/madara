"""Cloisonnement multi-clinique, managers, séquences et intégrité."""

from __future__ import annotations

import pytest
from django.core.exceptions import ValidationError
from django.db.utils import IntegrityError
from django.utils import timezone

from apps.accounts.models import Clinic, Membership
from apps.common.context import clinic_context, user_context
from apps.common.models import SequenceCounter
from apps.common.numbers import (
    format_phone_fr,
    next_sequence,
    normalize_phone,
    slugify_fr,
)
from tests.factories import ClinicFactory, MembershipFactory, UserFactory, make_staff

pytestmark = pytest.mark.django_db

CURRENT_YEAR = timezone.localdate().year


# ---------------------------------------------------------------------------
# Clinique
# ---------------------------------------------------------------------------


class TestClinic:
    def test_slug_genere_et_normalise(self):
        clinic = ClinicFactory.create(name="Clinique Médicale Al Amal")
        assert clinic.slug == "clinique-medicale-al-amal"

    def test_slug_unique_avec_suffixe_numerique(self):
        ClinicFactory.create(name="Clinique Al Amal")
        second = ClinicFactory.create(name="Clinique Al Amal")
        assert second.slug == "clinique-al-amal-2"

    def test_telephone_normalise_a_l_enregistrement(self):
        clinic = ClinicFactory.create(name="Clinique Test", phone="+212 6 12 34 56 78")
        clinic.refresh_from_db()
        assert clinic.phone == "+212612345678"

    def test_telephone_affiche_en_format_lisible(self):
        clinic = ClinicFactory.create(name="Clinique Test", phone="+212612345678")
        assert clinic.display_phone == "+212 612-345678"

    def test_medecins_de_la_clinique(self, clinic):
        make_staff(clinic, Membership.Role.DOCTOR)
        make_staff(clinic, Membership.Role.NURSE)
        assert clinic.doctors.count() == 1
        assert clinic.staff_count == 2

    def test_une_clinique_inactive_n_est_pas_listree(self, clinic):
        clinic.is_active = False
        clinic.save()
        assert not Clinic.objects.filter(slug=clinic.slug, is_active=True).exists()


# ---------------------------------------------------------------------------
# Cloisonnement par les managers
# ---------------------------------------------------------------------------


class TestClinicScoping:
    def test_manager_filtre_sur_la_clinique_courante(self, clinic, other_clinic):
        make_staff(clinic, Membership.Role.ADMIN)
        make_staff(other_clinic, Membership.Role.ADMIN)

        with clinic_context(clinic):
            assert Membership.objects.count() == 1
        with clinic_context(other_clinic):
            assert Membership.objects.count() == 1

    def test_all_objects_ignore_le_contexte(self, clinic, other_clinic):
        make_staff(clinic, Membership.Role.ADMIN)
        make_staff(other_clinic, Membership.Role.ADMIN)

        with clinic_context(clinic):
            assert Membership.all_objects.count() == 2

    def test_hors_contexte_aucun_filtre_n_est_applique(self, clinic, other_clinic):
        make_staff(clinic, Membership.Role.ADMIN)
        make_staff(other_clinic, Membership.Role.ADMIN)
        assert Membership.objects.count() == 2

    def test_administrateur_plateforme_voit_toutes_les_cliniques(
        self, platform_admin, clinic, other_clinic
    ):
        make_staff(clinic, Membership.Role.ADMIN)
        make_staff(other_clinic, Membership.Role.ADMIN)

        with clinic_context(clinic), user_context(platform_admin):
            assert Membership.objects.count() == 2

    def test_for_user_limite_aux_cliniques_de_l_utilisateur(self, clinic, other_clinic):
        user, membership = make_staff(clinic, Membership.Role.DOCTOR)
        assert list(Membership.objects.for_user(user)) == [membership]

    def test_for_user_avec_clinique_nulle_ne_renvoie_rien(self, clinic):
        make_staff(clinic, Membership.Role.DOCTOR)
        assert list(Membership.objects.for_clinic(None)) == []


# ---------------------------------------------------------------------------
# Séquences numérotées
# ---------------------------------------------------------------------------


class TestSequence:
    def test_premiere_reference(self, clinic):
        with clinic_context(clinic):
            assert next_sequence(clinic, "patient", "PT", digits=5) == (
                f"PT-{CURRENT_YEAR}-00001"
            )

    def test_increment_sans_repetition(self, clinic):
        with clinic_context(clinic):
            references = [next_sequence(clinic, "invoice", "FAC") for _ in range(5)]
        assert [reference.split("-")[-1] for reference in references] == [
            "0001",
            "0002",
            "0003",
            "0004",
            "0005",
        ]

    def test_compteurs_independants_par_clinique(self, clinic, other_clinic):
        with clinic_context(clinic):
            next_sequence(clinic, "invoice", "FAC")
        with clinic_context(other_clinic):
            assert next_sequence(other_clinic, "invoice", "FAC").endswith("-0001")

    def test_compteur_persiste_pour_audit(self, clinic):
        with clinic_context(clinic):
            next_sequence(clinic, "patient", "PT")
        counter = SequenceCounter.all_objects.get(clinic=clinic, kind="patient")
        assert (counter.last_value, counter.prefix, counter.period) == (
            1,
            "PT",
            str(CURRENT_YEAR),
        )

    def test_types_de_documents_independants(self, clinic):
        with clinic_context(clinic):
            assert next_sequence(clinic, "invoice", "FAC").endswith("-0001")
            assert next_sequence(clinic, "patient", "PT").endswith("-0001")


# ---------------------------------------------------------------------------
# Utilitaires de format
# ---------------------------------------------------------------------------


class TestFormatting:
    @pytest.mark.parametrize(
        ("entree", "attendu"),
        [
            ("+212 6 12 34 56 78", "+212612345678"),
            ("", ""),
            (None, ""),
        ],
    )
    def test_normalize_phone(self, entree, attendu):
        assert normalize_phone(entree) == attendu

    @pytest.mark.parametrize(
        ("entree", "attendu"),
        [
            ("+212612345678", "+212 612-345678"),
            ("+212537123456", "+212 537-123456"),
            ("0612345678", "0612-345678"),
            ("", ""),
        ],
    )
    def test_format_phone_fr(self, entree, attendu):
        assert format_phone_fr(entree) == attendu

    @pytest.mark.parametrize(
        ("entree", "attendu"),
        [
            ("Clinique Médicale Al Amal", "clinique-medicale-al-amal"),
            ("Clinique  Al   Amal", "clinique-al-amal"),
            ("Dr Amine Bennis", "dr-amine-bennis"),
            ("", "clinique"),
        ],
    )
    def test_slugify_fr(self, entree, attendu):
        assert slugify_fr(entree) == attendu


# ---------------------------------------------------------------------------
# Intégrité du personnel
# ---------------------------------------------------------------------------


class TestMembershipIntegrity:
    def test_un_seul_role_par_utilisateur_et_clinique(self, clinic):
        user, _ = make_staff(clinic, Membership.Role.DOCTOR)
        with pytest.raises(IntegrityError):
            Membership.objects.create(
                user=user, clinic=clinic, role=Membership.Role.NURSE
            )

    def test_memoire_de_patienteur_refusee(self, clinic):
        user, _ = make_staff(clinic, Membership.Role.DOCTOR)
        doublon = Membership(user=user, clinic=clinic, role=Membership.Role.NURSE)
        with pytest.raises(ValidationError):
            doublon.full_clean()

    def test_matricule_unique_par_clinique(self, clinic):
        MembershipFactory.create(
            clinic=clinic, employee_number="INF-001", role=Membership.Role.NURSE
        )
        with pytest.raises(IntegrityError):
            MembershipFactory.create(
                clinic=clinic, employee_number="INF-001", role=Membership.Role.ACCOUNTANT
            )

    def test_matricule_reutilisable_dans_une_autre_clinique(self, clinic, other_clinic):
        MembershipFactory.create(
            clinic=clinic, employee_number="DR-001", role=Membership.Role.DOCTOR
        )
        autre = MembershipFactory.create(
            clinic=other_clinic, employee_number="DR-001", role=Membership.Role.DOCTOR
        )
        assert autre.employee_number == "DR-001"

    def test_deux_matricules_vides_sont_autorises(self, clinic):
        first = MembershipFactory.create(clinic=clinic, employee_number="")
        second = MembershipFactory.create(clinic=clinic, employee_number="")
        assert first.pk != second.pk


class TestUser:
    def test_email_normalise_en_minuscules(self):
        user = UserFactory.create(email="Medecin@Example.MA")
        user.refresh_from_db()
        assert user.email == "medecin@example.ma"

    def test_clinics_ne_contient_que_les_memberships_actifs(self, clinic, other_clinic):
        user, membership = make_staff(clinic, Membership.Role.DOCTOR)
        make_staff(other_clinic, Membership.Role.DOCTOR)
        assert list(user.clinics) == [clinic]

        membership.is_active = False
        membership.save()
        assert list(user.clinics) == []

    def test_role_renseigne_le_role_du_membership(self, clinic):
        user, _ = make_staff(clinic, Membership.Role.NURSE)
        assert user.role == Membership.Role.NURSE

    def test_compte_sans_clinique_n_a_acces_a_aucune_clinique(self, db):
        user = UserFactory.create(email="orphelin@example.ma")
        assert user.has_active_membership() is False
        assert user.primary_membership is None
