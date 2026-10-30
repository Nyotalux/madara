"""Tests du tableau de bord et des rapports (jalon 7)."""

from __future__ import annotations

import pytest
from django.urls import reverse

from apps.dashboard.tasks import send_daily_report

pytestmark = pytest.mark.django_db


def test_dashboard_web_view(client, clinic, clinic_admin):
    session = client.session
    session["madara_clinic_id"] = clinic.pk
    session.save()
    client.force_login(clinic_admin)

    response = client.get(reverse("web:dashboard"))
    assert response.status_code == 200
    assert clinic.name in response.content.decode("utf-8")


def test_reporting_web_view(client, clinic, clinic_admin):
    session = client.session
    session["madara_clinic_id"] = clinic.pk
    session.save()
    client.force_login(clinic_admin)

    response = client.get(reverse("web:reporting"))
    assert response.status_code == 200
    assert "Rapports" in response.content.decode("utf-8")


def test_send_daily_report_task(clinic, clinic_admin):
    result = send_daily_report()
    assert result["clinics"] >= 1
    assert result["notices"] >= 1

    # Check that notification was created for clinic
    from apps.notifications.models import Notification

    assert Notification.objects.filter(clinic=clinic).exists()
