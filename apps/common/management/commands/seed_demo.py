"""Génère un jeu de données de démonstration (deux cliniques, équipe, planning).

    python manage.py seed_demo
    python manage.py seed_demo --flush     # repart de zéro

Les identifiants créés sont affichés à la fin de l'exécution.
"""

from __future__ import annotations

import random
from datetime import date, timedelta

from django.core.exceptions import ValidationError
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
from apps.appointments.models import Appointment, AppointmentCancellation
from apps.appointments.services import (
    available_slots,
    book_appointment,
    clinic_timezone,
    local_now,
)
from apps.common.context import clinic_context, user_context
from apps.patients.models import Patient

DEFAULT_PASSWORD = "Madara2026!"

FIRST_NAMES = [
    "Amine",
    "Salma",
    "Youssef",
    "Leila",
    "Karim",
    "Nadia",
    "Mehdi",
    "Sofia",
    "Anas",
    "Imane",
    "Rachid",
    "Hind",
    "Omar",
    "Fatima",
    "Yassine",
    "Meryem",
    "Adil",
    "Ghita",
    "Hamza",
    "Zineb",
]
LAST_NAMES = [
    "Benali",
    "El Amrani",
    "Tazi",
    "Bennis",
    "Chakir",
    "Fassi",
    "Idrissi",
    "Lamrani",
    "Ouazzani",
    "Sekkat",
    "Berrada",
    "Kettani",
    "Naciri",
    "Sabri",
]


def _slug(value: str) -> str:
    """Transforme un nom en fragment d'adresse e-mail ("El Amrani" -> "el-amrani")."""
    import unicodedata

    normalized = unicodedata.normalize("NFKD", value)
    ascii_only = normalized.encode("ascii", "ignore").decode("ascii").lower()
    return "-".join(part for part in ascii_only.replace("'", "").split() if part)


CITIES_STREETS = [
    "Hassan II",
    "Mohammed V",
    "Ibn Rochd",
    "Al Massira",
    "Anfa",
    "Zerktouni",
]

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
        parser.add_argument(
            "--agenda-days",
            type=int,
            default=7,
            help="Nombre de jours d'agenda remplis par clinique (défaut : 7).",
        )
        parser.add_argument(
            "--patients-per-clinic",
            type=int,
            default=40,
            help="Nombre de dossiers patients par clinique (défaut : 40).",
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
            patients = self._create_patients(
                clinic, staff, options["patients_per_clinic"]
            )
            appointments = self._create_appointments(
                clinic, staff, patients, options["agenda_days"]
            )
            self._link_platform_admin(platform, clinic)
            clinics.append((clinic, staff, patients, appointments))

        self.stdout.write(self.style.SUCCESS("\nJeu de démonstration prêt."))
        self.stdout.write(f"  Mot de passe commun : {options['password']}")
        self.stdout.write(f"  Administrateur plateforme : {platform.email}")
        for clinic, staff, patients, appointments in clinics:
            self.stdout.write(
                f"  {clinic.name} ({clinic.slug}) : "
                + ", ".join(f"{m.user.email} [{m.get_role_display()}]" for m in staff)
            )
            self.stdout.write(
                f"      {len(patients)} dossiers patients "
                f"(ex. {patients[0].reference} — {patients[0].display_name})"
                if patients
                else "      aucun dossier patient"
            )
            self.stdout.write(
                f"      {len(appointments)} rendez-vous sur {options['agenda_days']} jours"
                if appointments
                else "      aucun rendez-vous"
            )

    # -- étapes ------------------------------------------------------------

    def _flush(self):
        # Les rendez-vous protègent leur praticien : on les supprime d'abord.
        AppointmentCancellation.objects.all().delete()
        Appointment.all_objects.all().hard_delete()
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
            specialty, _ = Specialty.objects.get_or_create(
                name=name, defaults={"code": code}
            )
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
                            "start_time": "09:00",
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

    def _create_appointments(
        self, clinic, staff, patients, days: int
    ) -> list[Appointment]:
        """Agenda réaliste : créneaux libres respectés, statuts cohérents avec l'heure."""
        if not patients or days < 1:
            return []

        created: list[Appointment] = []
        tz = clinic_timezone(clinic)
        now = local_now(tz)
        practitioners = [
            m for m in staff if m.role in {Membership.Role.DOCTOR, Membership.Role.NURSE}
        ]
        reception = next((m for m in staff if m.role == Membership.Role.RECEPTION), None)
        reasons = [
            ("Consultation de suivi", ""),
            ("Douleurs lombaires", "Douleur lombaire basse, gêne à la marche."),
            ("Renouvellement d'ordonnance", ""),
            ("Bilan sanguin", "Prescription d'un bilan biologique."),
            ("Contrôle post-opératoire", "Contrôle à 15 jours de l'intervention."),
            ("Vaccination", ""),
            ("Certificat médical", ""),
        ]

        with clinic_context(clinic), user_context(reception.user if reception else None):
            for offset in range(days):
                day = now.date() + timedelta(days=offset)
                for practitioner in practitioners:
                    # ``include_past`` : la journée en cours doit déjà être
                    # remplie, sinon la démonstration ouvre sur un agenda vide.
                    slots = available_slots(
                        practitioner, day, clinic=clinic, tz=tz, include_past=True
                    )
                    # On garde environ la moitié des créneaux, pour un agenda réaliste.
                    kept = [s for i, s in enumerate(slots) if i % 2 == 0]
                    # Les deux derniers créneaux du jour en cours restent
                    # « arrived » si possible : la salle d'attente n'est jamais vide.
                    waiting = {len(kept) - 1, len(kept) - 2} if offset == 0 else set()
                    for index, (start_at, end_at) in enumerate(kept):
                        reason, notes = random.choice(reasons)
                        elapsed = now - start_at
                        if index in waiting and elapsed > timedelta(0):
                            status = Appointment.Status.ARRIVED
                        elif elapsed > timedelta(minutes=45):
                            status = Appointment.Status.DONE
                        elif elapsed > timedelta(0):
                            status = Appointment.Status.ARRIVED
                        else:
                            status = random.choice(
                                [
                                    Appointment.Status.CONFIRMED,
                                    Appointment.Status.CONFIRMED,
                                    Appointment.Status.PENDING,
                                ]
                            )
                        try:
                            appointment = book_appointment(
                                clinic=clinic,
                                patient=random.choice(patients),
                                practitioner=practitioner,
                                start_at=start_at,
                                end_at=end_at,
                                kind=random.choice(
                                    [
                                        Appointment.Kind.CONSULTATION,
                                        Appointment.Kind.CONSULTATION,
                                        Appointment.Kind.CONTROL,
                                    ]
                                ),
                                status=status,
                                reason=reason,
                                notes=notes,
                                user=reception.user if reception else None,
                            )
                        except ValidationError:
                            # Créneau déjà pris (hasard) : on l'ignore.
                            continue
                        created.append(appointment)

        return created

    def _create_patients(self, clinic, staff, count: int) -> list[Patient]:
        """Dossiers patients réalistes : identité, couverture, repères médicaux."""
        with clinic_context(clinic):
            if Patient.all_objects.filter(clinic=clinic).exists():
                self.stdout.write(
                    f"  {Patient.all_objects.filter(clinic=clinic).count()} dossiers "
                    "patients déjà présents : génération ignorée."
                )
                existing = list(Patient.objects.filter(clinic=clinic).order_by("id"))
                return existing[:count]

            doctors = [m for m in staff if m.role == Membership.Role.DOCTOR]
            cities = [clinic.city, "Casablanca", "Rabat", "Marrakech", "Fès", "Tanger"]
            insurers = ["CNSS", "AMO", "Mutuelle Al Amane", "AXA Santé", "Sanad"]
            created = []
            for index in range(count):
                first_name = FIRST_NAMES[index % len(FIRST_NAMES)]
                last_name = LAST_NAMES[(index * 5) % len(LAST_NAMES)]
                gender = random.choice(["F", "M"])
                birth_year = random.randint(1945, 2020)
                is_insured = random.random() < 0.7
                patient = Patient(
                    clinic=clinic,
                    first_name=first_name,
                    last_name=last_name,
                    gender=gender,
                    birth_date=date(
                        birth_year, random.randint(1, 12), random.randint(1, 28)
                    ),
                    cin=f"{random.choice('AB')}{random.randint(100000, 999999)}",
                    phone=f"+2126{random.randint(10000000, 99999999)}",
                    email=f"{_slug(first_name)}.{_slug(last_name)}.{index}@example.ma",
                    city=random.choice(cities),
                    address=f"{random.randint(1, 200)} rue {random.choice(CITIES_STREETS)}",
                    emergency_contact_name=f"{random.choice(FIRST_NAMES)} {last_name}",
                    emergency_contact_phone=f"+2126{random.randint(10000000, 99999999)}",
                    is_insured=is_insured,
                    insurer=random.choice(insurers) if is_insured else "",
                    insurance_number=f"{random.randint(100000, 999999)}"
                    if is_insured
                    else "",
                    insurance_expiry=(
                        date(2027, 1, 31)
                        if is_insured and random.random() < 0.8
                        else None
                    ),
                    blood_type=random.choice(["A+", "B+", "O+", "O-", "AB+", ""]),
                    height_cm=random.randint(150, 195),
                    weight_kg=round(random.uniform(45, 105), 1),
                    allergies=random.choice(
                        ["", "", "", "Pénicilline", "Arachides", "Poussière"]
                    ),
                    chronic_conditions=random.choice(
                        ["", "", "", "Hypertension", "Diabète de type 2", "Asthme"]
                    ),
                    current_medication=random.choice(
                        ["", "", "", "Amlodipine 5 mg", "Metformine 850 mg", "Salbutamol"]
                    ),
                    is_pregnant=gender == "F"
                    and birth_year >= 1990
                    and random.random() < 0.15,
                    notes="",
                )
                if doctors and index % 10 == 0:
                    patient.created_by = random.choice(doctors).user
                patient.save()
                created.append(patient)

            # Un dossier archivé par clinique, pour illustrer le filtre.
            if created:
                archived = created[0]
                archived.archive("Dossier clos (démo) — patient transféré")
            self.stdout.write(f"  {len(created)} dossiers patients créés.")
            return created
