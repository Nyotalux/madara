"""Tâches de fond de l'agenda : agenda du jour et rappels (jalon 5).

Elles alimentent la file de notifications via ``apps.notifications.services`` :
l'envoi concret est effectué par ``notifications.dispatch_pending``.
"""

from __future__ import annotations

import logging

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(name="appointments.tasks.send_daily_agenda")
def send_daily_agenda(day=None):
    """Agenda du matin : message in-app + e-mail à chaque praticien (07:00).

    Parmi les membres actifs de chaque clinique, reçoivent la synthèse les
    praticiens (médecin, infirmier) et l'administrateur de clinique ;
    la comptabilité n'a pas besoin de l'agenda médical.
    """
    from apps.accounts.models import Clinic, Membership

    from .services import agenda_notice

    summary = {"clinics": 0, "notices": 0, "emails": 0}
    for clinic in Clinic.objects.filter(is_active=True):
        summary["clinics"] += 1
        for membership in (
            clinic.memberships.filter(is_active=True)
            .exclude(role=Membership.Role.ACCOUNTANT)
            .select_related("user")
        ):
            for notice in agenda_notice(clinic, membership, day=day):
                if notice is None:
                    continue
                notice.full_clean()
                notice.save()
                summary["notices"] += 1
                if notice.channel == "EMAIL":
                    summary["emails"] += 1

    logger.info("Agenda du jour diffusé : %s", summary)
    return summary


@shared_task(name="appointments.tasks.send_upcoming_reminders")
def send_upcoming_reminders():
    """Rappels de rendez-vous (toutes les 10 minutes).

    Filet de sécurité : les rendez-vous proches qui ne sont couverts par
    aucune notification en attente reçoivent un rappel in-app praticien et,
    si possible, un e-mail patient immédiat.
    """
    from apps.notifications.services import send_imminent_reminders

    return send_imminent_reminders()
