"""Routage web unique, namespace « web ».

Django ne fusionne pas deux modules déclarant le même ``app_name`` : toutes les
applications contribute ici, et une seule fois, au namespace ``web``.
Les modules ``apps/<app>/urls_web.py`` exposent une simple liste de ``urlpatterns``.
"""

from __future__ import annotations

from django.urls import include, path

app_name = "web"

urlpatterns = [
    # --- Comptes, cliniques et personnel
    path("", include("apps.accounts.urls_web")),
    # --- Tableau de bord (jalon 1)
    path("", include("apps.dashboard.urls_web")),
    # --- Dossiers patients (jalon 2)
    path("", include("apps.patients.urls_web")),
    # --- Agenda et rendez-vous (jalon 3)
    path("", include("apps.appointments.urls_web")),
    # path("", include("apps.consultations.urls_web")),
    # path("", include("apps.medical_records.urls_web")),
    # path("", include("apps.billing.urls_web")),
    # path("", include("apps.inventory.urls_web")),
    # path("", include("apps.payroll.urls_web")),
    # path("", include("apps.notifications.urls_web")),
]
