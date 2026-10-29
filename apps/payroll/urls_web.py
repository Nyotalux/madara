"""Routes web de la paie (namespace ``web``)."""

from __future__ import annotations

from django.urls import path

from . import views

urlpatterns = [
    path("salaires/", views.payroll_home, name="payroll-home"),
    path("salaires/primes/nouvelle/", views.bonus_create, name="bonus-create"),
    path(
        "salaires/primes/<int:pk>/retirer/",
        views.bonus_remove,
        name="bonus-remove",
    ),
    path(
        "salaires/periode/<str:period>/",
        views.period_detail,
        name="period-detail",
    ),
    path(
        "salaires/periode/<str:period>/generer/",
        views.period_generate,
        name="period-generate",
    ),
    path("salaires/bulletins/<int:pk>/", views.payslip_detail, name="payslip-detail"),
    path(
        "salaires/bulletins/<int:pk>/modifier/",
        views.payslip_edit,
        name="payslip-edit",
    ),
    path("salaires/bulletins/<int:pk>/payer/", views.payslip_pay, name="payslip-pay"),
    path(
        "salaires/bulletins/<int:pk>/annuler/",
        views.payslip_cancel,
        name="payslip-cancel",
    ),
]
