"""Dossiers médicaux, documents et partages : règle métier, web et API (jalon 4)."""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import Membership
from apps.common.context import clinic_context
from apps.medical_records import services
from apps.medical_records.models import MedicalDocument, MedicalRecordShare
from apps.patients.models import Patient

from .factories import UserFactory, make_staff

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _media_root(tmp_path, settings):
    settings.MEDIA_ROOT = tmp_path
    return tmp_path


def make_patient(clinic, **kwargs):
    defaults = {
        "clinic": clinic,
        "first_name": "Yasmine",
        "last_name": "Idrissi",
        "phone": "+212612345678",
    }
    defaults.update(kwargs)
    return Patient.objects.create(**defaults)


def pdf(name="bilan.pdf"):
    return SimpleUploadedFile(name, b"%PDF-1.4 contenu", content_type="application/pdf")


@pytest.fixture
def team(clinic, other_clinic):
    doctor, doctor_membership = make_staff(
        clinic, Membership.Role.DOCTOR, first_name="Salma", last_name="Bennis"
    )
    reception, reception_membership = make_staff(
        clinic, Membership.Role.RECEPTION, first_name="Nadia", last_name="Amrani"
    )
    rival, rival_membership = make_staff(
        other_clinic, Membership.Role.DOCTOR, first_name="Rachid", last_name="Idrissi"
    )
    return {
        "clinic": clinic,
        "other_clinic": other_clinic,
        "doctor": doctor,
        "doctor_membership": doctor_membership,
        "reception": reception,
        "reception_membership": reception_membership,
        "rival": rival,
        "rival_membership": rival_membership,
    }


@pytest.fixture
def patient(clinic):
    return make_patient(clinic)


@pytest.fixture
def record(clinic, patient):
    with clinic_context(clinic):
        return services.ensure_record(patient)


# ---------------------------------------------------------------------------
# Règles métier
# ---------------------------------------------------------------------------


def test_ensure_record_is_idempotent(record, patient):
    assert record.reference.startswith("DM-")
    with clinic_context(patient.clinic):
        again = services.ensure_record(patient)
    assert again.pk == record.pk
    assert MedicalRecordShare.objects.count() == 0


def test_add_document_validates_and_links(record):
    with clinic_context(record.patient.clinic):
        document = services.add_document(
            record,
            category=MedicalDocument.Category.LAB,
            title="Bilan sanguin",
            file=pdf(),
        )
    assert document.record_id == record.pk
    assert document.clinic_id == record.patient.clinic_id
    assert document.filename.endswith(".pdf")
    assert document.extension == "pdf"


def test_add_document_rejects_bad_extension(record):
    with clinic_context(record.patient.clinic):
        with pytest.raises(ValidationError):
            services.add_document(
                record,
                category=MedicalDocument.Category.OTHER,
                title="Programme",
                file=SimpleUploadedFile("virus.exe", b"MZ"),
            )


def test_add_document_rejects_future_date(record):
    with clinic_context(record.patient.clinic):
        with pytest.raises(ValidationError):
            services.add_document(
                record,
                category=MedicalDocument.Category.OTHER,
                title="Futur",
                file=pdf(),
                document_date=timezone.localdate() + timedelta(days=1),
            )


def test_create_share_is_bounded(record):
    with clinic_context(record.patient.clinic):
        share = services.create_share(record, days=7, shared_with="Dr Ahmed")
    assert share.shared_with == "Dr Ahmed"
    assert share.is_valid
    assert share.status_label == "Actif"

    with clinic_context(record.patient.clinic):
        with pytest.raises(ValidationError):
            services.create_share(record, days=365)


def test_revoke_share_makes_token_invalid(record):
    with clinic_context(record.patient.clinic):
        share = services.create_share(record, days=7)
    services.revoke_share(share)
    share.refresh_from_db()
    assert not share.is_valid
    assert share.status_label == "Révoqué"


# ---------------------------------------------------------------------------
# Web
# ---------------------------------------------------------------------------


def test_web_record_detail_creates_record(client, team, patient):
    client.force_login(team["reception"])
    response = client.get(reverse("web:medical-record", args=[patient.pk]))
    assert response.status_code == 200
    assert "Antécédents familiaux" in response.content.decode()
    with clinic_context(team["clinic"]):
        record = services.ensure_record(patient)
    assert record.reference.encode() in response.content


def test_web_record_edit_saves_history(client, team, patient, record):
    client.force_login(team["doctor"])
    response = client.post(
        reverse("web:medical-record-edit", args=[patient.pk]),
        {
            "family_history": "Père diabétique",
            "medical_history": "Asthme",
            "surgical_history": "",
            "gynecological_history": "",
            "vaccinations": "À jour",
            "lifestyle": "Non fumeur",
            "disabilities": "",
            "notes": "RAS",
        },
    )
    assert response.status_code == 302
    record.refresh_from_db()
    assert record.medical_history == "Asthme"
    assert record.last_reviewed_at is not None


def test_reception_can_add_document(client, team, patient):
    client.force_login(team["reception"])
    response = client.post(
        reverse("web:medical-document-add", args=[patient.pk]),
        {
            "category": MedicalDocument.Category.LAB,
            "title": "Numération formule sanguine",
            "document_date": timezone.localdate().isoformat(),
            "description": "",
            "file": pdf("nfs.pdf"),
        },
    )
    assert response.status_code == 302
    assert MedicalDocument.objects.filter(title="Numération formule sanguine").exists()


def test_web_document_rejects_bad_extension(client, team, patient, record):
    client.force_login(team["reception"])
    response = client.post(
        reverse("web:medical-document-add", args=[patient.pk]),
        {
            "category": MedicalDocument.Category.OTHER,
            "title": "Document",
            "document_date": "",
            "description": "",
            "file": SimpleUploadedFile("virus.exe", b"MZ"),
        },
    )
    assert response.status_code == 200
    assert not MedicalDocument.objects.filter(title="Document").exists()


def test_web_document_delete_archives(client, team, patient, record):
    with clinic_context(team["clinic"]):
        document = services.add_document(
            record,
            category=MedicalDocument.Category.OTHER,
            title="À retirer",
            file=pdf(),
        )
    client.force_login(team["reception"])
    page = client.get(reverse("web:medical-document-delete", args=[document.pk]))
    assert page.status_code == 200
    response = client.post(reverse("web:medical-document-delete", args=[document.pk]))
    assert response.status_code == 302
    document.refresh_from_db()
    assert not document.is_active


def test_share_link_is_public_until_revoked(client, team, patient, record):
    with clinic_context(team["clinic"]):
        share = services.create_share(record, days=7, shared_with="Dr Ahmed")
    # Accès public, sans connexion.
    response = client.get(reverse("web:medical-record-shared", args=[share.token]))
    assert response.status_code == 200
    assert patient.display_name.encode() in response.content

    client.force_login(team["reception"])
    response = client.post(reverse("web:medical-record-share-revoke", args=[share.pk]))
    assert response.status_code == 302
    client.logout()
    assert (
        client.get(reverse("web:medical-record-shared", args=[share.token])).status_code
        == 404
    )


def test_web_share_create(client, team, patient, record):
    client.force_login(team["reception"])
    response = client.post(
        reverse("web:medical-record-share", args=[patient.pk]),
        {"days": 7, "shared_with": "Confrère", "note": "Avis"},
    )
    assert response.status_code == 302
    share = MedicalRecordShare.objects.get(record=record)
    assert share.shared_with == "Confrère"


def test_cross_clinic_record_is_not_reachable(client, team):
    other_patient = make_patient(
        team["other_clinic"], first_name="Rim", last_name="Sabri"
    )
    client.force_login(team["rival"])
    # Le médecin rival voit le dossier de sa propre clinique…
    assert (
        client.get(reverse("web:medical-record", args=[other_patient.pk])).status_code
        == 200
    )
    # … mais pas celui d'une autre clinique.
    mine = make_patient(team["clinic"], first_name="Ali", last_name="Amrani")
    assert client.get(reverse("web:medical-record", args=[mine.pk])).status_code == 404


def test_admin_medical_record_pages_render(client, team, patient, record):
    platform_admin = UserFactory.create(
        email="superadmin@example.ma",
        is_staff=True,
        is_superuser=True,
        is_platform_staff=True,
    )
    client.force_login(platform_admin)
    assert client.get("/admin/medical_records/medicalrecord/").status_code == 200
    assert (
        client.get(
            f"/admin/medical_records/medicalrecord/{record.pk}/change/"
        ).status_code
        == 200
    )
    assert client.get("/admin/medical_records/medicaldocument/").status_code == 200
    assert client.get("/admin/medical_records/medicalrecordshare/").status_code == 200


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------


def test_api_lists_only_current_clinic(team, record):
    other_patient = make_patient(
        team["other_clinic"], first_name="Rim", last_name="Sabri"
    )
    with clinic_context(team["other_clinic"]):
        services.ensure_record(other_patient)

    api = APIClient()
    api.force_authenticate(user=team["doctor"])
    response = api.get("/api/v1/medical-records/")
    assert response.status_code == 200
    payload = response.json()["results"]
    assert [row["reference"] for row in payload] == [record.reference]


def test_api_patch_record_history(team, record):
    api = APIClient()
    api.force_authenticate(user=team["doctor"])
    response = api.patch(
        f"/api/v1/medical-records/{record.pk}/",
        {"medical_history": "Hypertension"},
        format="json",
    )
    assert response.status_code == 200, response.json()
    record.refresh_from_db()
    assert record.medical_history == "Hypertension"


def test_api_create_document(team, record):
    api = APIClient()
    api.force_authenticate(user=team["reception"])
    response = api.post(
        "/api/v1/medical-documents/",
        {
            "record": record.pk,
            "category": MedicalDocument.Category.IMAGING,
            "title": "Radiographie",
            "file": pdf("radio.pdf"),
        },
        format="multipart",
    )
    assert response.status_code == 201, response.json()
    assert response.json()["extension"] == "pdf"


def test_api_share_create_and_revoke(team, record):
    api = APIClient()
    api.force_authenticate(user=team["doctor"])
    created = api.post(
        "/api/v1/medical-shares/",
        {"record": record.pk, "days": 7, "shared_with": "Dr Ahmed"},
        format="json",
    )
    assert created.status_code == 201, created.json()
    share_id = created.json()["id"]
    assert created.json()["is_valid"] is True

    revoked = api.post(f"/api/v1/medical-shares/{share_id}/revoke/")
    assert revoked.status_code == 200
    assert revoked.json()["is_valid"] is False


def test_api_refuses_accountant(team):
    accountant, _ = make_staff(team["clinic"], Membership.Role.ACCOUNTANT)
    api = APIClient()
    api.force_authenticate(user=accountant)
    assert api.get("/api/v1/medical-records/").status_code == 403
