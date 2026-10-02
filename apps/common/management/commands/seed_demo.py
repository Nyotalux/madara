"""Génère un jeu de données de démonstration (deux cliniques, équipe, planning).

    python manage.py seed_demo
    python manage.py seed_demo --flush     # repart de zéro

Les identifiants créés sont affichés à la fin de l'exécution.
"""

from __future__ import annotations

import random
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from apps.accounts.models import (
    BlockedSlot,
    Clinic,
    Membership,
    OpeningHour,
    PractitionerProfile,
    Specialty,
    User,
)
from apps.common.context import clinic_context, user_context
from apps.common.numbers import next_sequence

DEFAULT_PASSWORD = "Madara2026!"

FIRST_NAMES = [
    "Amine", "Salma", "Youssef", "Leila", "Karim", "Nadia", "Mehdi", "Sofia",
    "Anas", "Imane", "Rachid", "Hind", "Omar", "Fatima", "Yassine", "Meryem",
    "Adil", "Ghita", "Hamza", "Zineb",
]
LAST_NAMES = [
    "Benali", "El Amrani", "Tazi", "Bennis", "Chakir", "Fassi", "Idrissi",
    "Lamrani", "Ouazzani", "Sekkat", "Berrada", "Kettani", "Naciri", "Sabri",
]

def _slug(value: str) -> str:
    """Transforme un nom en fragment d'adresse e-mail ("El Amrani" -> "el-amrani")."""
    import unicodedata

    normalized = unicodedata.normalize("NFKD", value)
    ascii_only = normalized.encode("ascii", "ignore").decode("ascii").lower()
    return "-".join(part for part in ascii_only.replace("'", "").split() if part)


SPECIALTIES = [
    ("Médecine générale", "GEN"),
    ("Cardiologie", "CAR"),
    ("Pédiatrie", "PED"),
    ("Dermatologie", "DER"),
    ("Gynécologie", "GYN"),
    ("Orthopédie", "ORT"),
]


class Command(BaseCommand):
    help = "Crée un jeu de données de démonstration pour Madara."

    def add_arguments(self, parser):
        parser.add_argument(
            "--flush",
            action="store_true",
            help="Supprime les données existantes avant de générer.",
        )
        parser.add_argument(
            "--password",
            default=DEFAULT_PASSWORD,
            help="Mot de passe attribué à tous les comptes créés.",
        )
        parser.add_argument(
            "--staff-per-clinic",
            type=int,
            default=6,
            help="Nombre de médecins par clinique (défaut : 6).",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        random.seed(20260101)

        if options["flush"]:
            self._flush()

        platform = self._create_platform_admin(options["password"])
        specialties = self._create_specialties()

        clinics = []
        for index, (name, city) in enumerate(
            [("Clinique Al Amal", "Casablanca"), ("Clinique Essaouira", "Rabat")],
            start=1,
        ):
            clinic = self._create_clinic(name, city, index)
            staff = self._create_staff(clinic, specialties, options)
            self._create_opening_hours(clinic, staff)
            self._create_blocked_slots(clinic, staff)
            self._link_platform_admin(platform, clinic)
            clinics.append((clinic, staff))

        self.stdout.write(self.style.SUCCESS("\nJeu de démonstration prêt."))
        self.stdout.write(f"  Mot de passe commun : {options['password']}")
        self.stdout.write(f"  Administrateur plateforme : {platform.email}")
        for clinic, staff in clinics:
            self.stdout.write(
                f"  {clinic.name} ({clinic.slug}) : "
                + ", ".join(f"{m.user.email} [{m.get_role_display()}]" for m in staff)
            )

    # -- étapes ------------------------------------------------------------

    def _flush(self):
        Clinic.objects.all().delete()
        User.objects.filter(is_platform_staff=True).delete()
        self.stdout.write(self.style.WARNING("Données existantes supprimées."))

    def _create_platform_admin(self, password) -> User:
        email = "admin@madara.ma"
        user, created = User.objects.get_or_create(
            email=email,
            defaults={
                "first_name": "Administration",
                "last_name": "Madara",
                "is_staff": True,
                "is_superuser": True,
                "is_platform_staff": True,
            },
        )
        if created:
            user.set_password(password)
            user.save()
        return user

    def _create_specialties(self) -> list[Specialty]:
        specialties = []
        for name, code in SPECIALTIES:
            specialty, _ = Specialty.objects.get_or_create(name=name, defaults={"code": code})
            specialties.append(specialty)
        return specialties

    def _create_clinic(self, name, city, index) -> Clinic:
        clinic, created = Clinic.objects.get_or_create(
            name=name,
            defaults={
                "legal_name": f"{name} SARL",
                "address": f"{index * 12} boulevard Mohammed V",
                "city": city,
                "country": "Maroc",
                "phone": f"+212522{index:03d}{index:03d}",
                "emergency_phone": f"+212522{index:03d}{index:02d}00",
                "email": f"contact@{name.split()[1].lower()}.ma",
                "tax_identifier": f"IF{index:07d}",
                "license_number": f"AG-{2026}-{index:04d}",
                "currency": "MAD",
                "tax_rate": 0,
            },
        )
        if created:
            self.stdout.write(f"  Clinique créée : {clinic.name} ({clinic.slug})")
        return clinic

    def _create_staff(self, clinic, specialties, options) -> list[Membership]:
        with clinic_context(clinic):
            memberships = []
            for suffix, role in [
                ("admin", Membership.Role.ADMIN),
                ("reception", Membership.Role.RECEPTION),
                ("comptable", Membership.Role.ACCOUNTANT),
            ]:
                memberships.append(
                    self._create_member(
                        clinic,
                        f"{suffix}@{clinic.slug}.ma",
                        f"{suffix.capitalize()}",
                        role,
                        options["password"],
                        employee_number=f"{suffix[:3].upper()}-001",
                    )
                )

            doctor_count = options["staff_per_clinic"]
            for index in range(doctor_count):
                specialty = specialties[index % len(specialties)]
                first_name = FIRST_NAMES[index % len(FIRST_NAMES)]
                last_name = LAST_NAMES[(index * 3) % len(LAST_NAMES)]
                membership = self._create_member(
                    clinic,
                    f"dr.{_slug(first_name)}.{_slug(last_name)}@{clinic.slug}.ma",
                    f"Dr {first_name} {last_name}",
                    Membership.Role.DOCTOR,
                    options["password"],
                    last_name=last_name,
                    first_name=first_name,
                    employee_number=f"DR-{index + 1:03d}",
                )
                PractitionerProfile.objects.update_or_create(
                    membership=membership,
                    defaults={
                        "profession": PractitionerProfile.Profession.DOCTOR,
                        "specialty": specialty,
                        "license_number": f"MED-{clinic.id}-{index + 1:04d}",
                        "years_of_experience": 3 + index * 2,
                        "consultation_duration_minutes": 20 if index % 2 == 0 else 30,
                    },
                )
                memberships.append(membership)

            for index in range(2):
                first_name = FIRST_NAMES[(index + 7) % len(FIRST_NAMES)]
                last_name = LAST_NAMES[(index + 11) % len(LAST_NAMES)]
                membership = self._create_member(
                    clinic,
                    f"inf.{_slug(first_name)}.{_slug(last_name)}@{clinic.slug}.ma",
                    f"{first_name} {last_name}",
                    Membership.Role.NURSE,
                    options["password"],
                    last_name=last_name,
                    first_name=first_name,
                    employee_number=f"INF-{index + 1:03d}",
                )
                PractitionerProfile.objects.update_or_create(
                    membership=membership,
                    defaults={
                        "profession": PractitionerProfile.Profession.NURSE,
                        "license_number": f"INF-{clinic.id}-{index + 1:04d}",
                        "years_of_experience": 2 + index * 3,
                    },
                )
                memberships.append(membership)

        return memberships

    def _create_member(
        self,
        clinic,
        email,
        full_name,
        role,
        password,
        last_name="",
        first_name="",
        employee_number="",
    ) -> Membership:
        first_name = first_name or full_name.split(" ")[-1]
        last_name = last_name or " ".join(full_name.split(" ")[1:])
        user, created = User.objects.get_or_create(
            email=email,
            defaults={
                "first_name": first_name,
                "last_name": last_name,
                "phone": f"+2126{random.randint(10000000, 99999999)}",
            },
        )
        if created:
            user.set_password(password)
            user.save()

        membership, _ = Membership.objects.get_or_create(
            user=user,
            clinic=clinic,
            defaults={
                "role": role,
                "employee_number": employee_number,
                "hire_date": timezone.localdate() - timedelta(days=400),
            },
        )
        return membership

    def _create_opening_hours(self, clinic, staff):
        with clinic_context(clinic):
            for membership in staff:
                if membership.role not in {
                    Membership.Role.DOCTOR,
                    Membership.Role.NURSE,
                }:
                    continue
                for weekday in range(0, 6):
                    is_closed = weekday == 5 and membership.role == Membership.Role.DOCTOR
                    OpeningHour.objects.get_or_create(
                        membership=membership,
                        weekday=weekday,
                        defaults={
                            "start_time": "09:00" if weekday < 5 else "09:00",
                            "end_time": "13:00" if weekday < 5 else "14:00",
                            "is_closed": is_closed,
                        },
                    )

    def _create_blocked_slots(self, clinic, staff):
        with clinic_context(clinic):
            doctors = [m for m in staff if m.role == Membership.Role.DOCTOR]
            now = timezone.now()
            for index, membership in enumerate(doctors[:2]):
                BlockedSlot.objects.get_or_create(
                    membership=membership,
                    start_at=now + timedelta(days=index + 2, hours=9),
                    defaults={
                        "end_at": now + timedelta(days=index + 2, hours=13),
                        "reason": "Formation médicale continue",
                    },
                )

    def _link_platform_admin(self, user, clinic):
        with user_context(user), clinic_context(clinic):
            Membership.objects.get_or_create(
                user=user,
                clinic=clinic,
                defaults={"role": Membership.Role.ADMIN},
            )

    def _preview_references(self, clinic):
        """Vérifie que le compteur de séquences est prêt (numérotation du jalon 2)."""
        with clinic_context(clinic):
            next_sequence(clinic, "patient", "PT", digits=5)