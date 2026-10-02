"""Fixtures et fabriques partagées : cliniques, utilisateurs, rôles."""

from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from factory import Faker, LazyAttribute, SubFactory
from factory.django import DjangoModelFactory
from faker import Faker as FakerGenerator

from apps.accounts.models import (
    Clinic,
    Membership,
    OpeningHour,
    PractitionerProfile,
    Specialty,
)

User = get_user_model()

PASSWORD = "TestMotDePasse2026!"

fake = FakerGenerator("fr_FR")


def unique_email() -> str:
    """E-mail unique par build, en français."""
    return fake.unique.email()


# ---------------------------------------------------------------------------
# Fabriques
# ---------------------------------------------------------------------------


class ClinicFactory(DjangoModelFactory):
    class Meta:
        model = Clinic

    name = Faker("company", locale="fr_FR")
    address = Faker("address", locale="fr_FR")
    city = Faker("city", locale="fr_FR")
    phone = "+212522000000"
    tax_rate = "0.00"


class UserFactory(DjangoModelFactory):
    class Meta:
        model = User

    email = LazyAttribute(lambda _: unique_email())
    first_name = Faker("first_name", locale="fr_FR")
    last_name = Faker("last_name", locale="fr_FR")

    @classmethod
    def _create(cls, model_class, *args, **kwargs):
        instance = model_class(**kwargs)
        instance.set_password(PASSWORD)
        instance.save()
        return instance


class MembershipFactory(DjangoModelFactory):
    class Meta:
        model = Membership

    user = SubFactory(UserFactory)
    clinic = SubFactory(ClinicFactory)
    role = Membership.Role.DOCTOR


class SpecialtyFactory(DjangoModelFactory):
    class Meta:
        model = Specialty
        django_get_or_create = ["name"]

    name = Faker("word", locale="fr_FR")
    code = Faker("bothify", text="????")


class PractitionerProfileFactory(DjangoModelFactory):
    class Meta:
        model = PractitionerProfile

    membership = SubFactory(MembershipFactory)
    profession = PractitionerProfile.Profession.DOCTOR


# ---------------------------------------------------------------------------
# Raccourcis
# ---------------------------------------------------------------------------


def make_staff(clinic, role: str, *, first_name="Amine", last_name="Benali"):
    """Crée un utilisateur avec un rôle dans la clinique donnée.

    L'e-mail est suffixé par le nombre d'utilisateurs déjà créés : deux
    praticiens homonymes dans la même clinique ne se chevauchent pas.
    """
    user = UserFactory.create(
        email=(
            f"{role.lower()}.{last_name.lower()}.{clinic.pk}."
            f"{User.objects.count()}@example.ma"
        ),
        first_name=first_name,
        last_name=last_name,
    )
    membership = Membership.objects.create(user=user, clinic=clinic, role=role)
    if role == Membership.Role.DOCTOR:
        PractitionerProfile.objects.create(
            membership=membership,
            profession=PractitionerProfile.Profession.DOCTOR,
            specialty=SpecialtyFactory.create(),
        )
    if role in {Membership.Role.DOCTOR, Membership.Role.NURSE}:
        create_opening_hours(membership)
    return user, membership


def create_opening_hours(membership, *, start="09:00", end="17:00"):
    """Horaires hebdomadaires : une fenêtre par jour ouvré (contrainte jalon 1)."""
    from datetime import time

    hours = []
    for weekday in range(0, 6):
        hours.append(
            OpeningHour.objects.create(
                membership=membership,
                weekday=weekday,
                start_time=time(*(int(part) for part in start.split(":"))),
                end_time=time(*(int(part) for part in end.split(":"))),
                is_closed=weekday == 5 and membership.role == Membership.Role.DOCTOR,
            )
        )
    return hours


@pytest.fixture
def clinic(db):
    return ClinicFactory.create(name="Clinique Al Amal", city="Casablanca")


@pytest.fixture
def other_clinic(db):
    return ClinicFactory.create(name="Clinique Essaouira", city="Rabat")


@pytest.fixture
def platform_admin(db):
    return UserFactory.create(
        email="plateforme@example.ma", is_platform_staff=True, is_staff=True
    )


@pytest.fixture
def doctor(clinic):
    return make_staff(clinic, Membership.Role.DOCTOR, first_name="Salma")[0]


@pytest.fixture
def nurse(clinic):
    return make_staff(clinic, Membership.Role.NURSE, first_name="Sofia")[0]


@pytest.fixture
def receptionist(clinic):
    return make_staff(clinic, Membership.Role.RECEPTION, first_name="Nadia")[0]


@pytest.fixture
def accountant(clinic):
    return make_staff(clinic, Membership.Role.ACCOUNTANT, first_name="Hamza")[0]


@pytest.fixture
def clinic_admin(clinic):
    return make_staff(clinic, Membership.Role.ADMIN, first_name="Karim")[0]


@pytest.fixture
def logged_doctor(client, doctor):
    client.force_login(doctor)
    return doctor


@pytest.fixture
def logged_clinic_admin(client, clinic_admin):
    client.force_login(clinic_admin)
    return clinic_admin


@pytest.fixture
def doctor_membership(doctor):
    return doctor.memberships.first()
