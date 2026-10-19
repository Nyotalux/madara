"""Routes web de la facturation (namespace ``web``)."""

from __future__ import annotations

from django.urls import path

from . import views

urlpatterns = [
    path("factures/", views.invoice_list, name="invoice-list"),
    path("factures/nouveau/", views.invoice_create, name="invoice-create"),
    path("factures/<int:pk>/", views.invoice_detail, name="invoice-detail"),
    path("factures/<int:pk>/modifier/", views.invoice_edit, name="invoice-edit"),
    path("factures/<int:pk>/emettre/", views.invoice_issue, name="invoice-issue"),
    path("factures/<int:pk>/annuler/", views.invoice_cancel, name="invoice-cancel"),
    path("factures/<int:pk>/paiement/", views.invoice_payment, name="invoice-payment"),
    path("factures/<int:pk>/imprimer/", views.invoice_print, name="invoice-print"),
    path(
        "factures/<int:pk>/paiement/<int:payment_pk>/retirer/",
        views.payment_delete,
        name="payment-delete",
    ),
]
