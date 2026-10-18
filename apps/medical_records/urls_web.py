"""Routes web du dossier médical (namespace ``web``)."""

from __future__ import annotations

from django.urls import path

from . import views

urlpatterns = [
    # Dossier rattaché au patient (jalon 4).
    path(
        "patients/<int:pk>/dossier/",
        views.record_detail,
        name="medical-record",
    ),
    path(
        "patients/<int:pk>/dossier/modifier/",
        views.record_edit,
        name="medical-record-edit",
    ),
    path(
        "patients/<int:pk>/dossier/document/",
        views.document_add,
        name="medical-document-add",
    ),
    path(
        "patients/<int:pk>/dossier/partage/",
        views.share_create,
        name="medical-record-share",
    ),
    path(
        "dossiers/documents/<int:pk>/retirer/",
        views.document_delete,
        name="medical-document-delete",
    ),
    path(
        "dossiers/partages/<int:pk>/revoquer/",
        views.share_revoke,
        name="medical-record-share-revoke",
    ),
    # Accès public en lecture seule via jeton (sans session, sans clinique courante).
    path(
        "dossier/partage/<uuid:token>/",
        views.shared_record,
        name="medical-record-shared",
    ),
]
