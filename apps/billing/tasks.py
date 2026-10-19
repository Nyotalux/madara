"""Tâches de fond de la facturation (cadenas dans ``config/celery.py``)."""

from __future__ import annotations

import logging

from celery import shared_task
from django.utils.translation import gettext_lazy as _

logger = logging.getLogger(__name__)


@shared_task(name="billing.tasks.send_overdue_invoice_reminders")
def send_overdue_invoice_reminders():
    """Relance des factures impayées (tous les jours à 09:00).

    Pour chaque clinique : les patients des factures échues reçoivent une
    relance WhatsApp puis e-mail (canaux disponibles) ; l'administration et
    la comptabilité reçoivent une synthèse in-app. Cadence de relance :
    première relance après ``clinic.dunning_days`` jours d'échéance, puis
    toutes les 7 jours.
    """
    from apps.accounts.models import Clinic, Membership
    from apps.notifications.models import Notification
    from apps.notifications.services import queue_notification

    from .services import overdue_invoices

    summary = {"clinics": 0, "patients_relanced": 0, "notices": 0}
    for clinic in Clinic.objects.filter(is_active=True):
        overdue = [
            invoice
            for invoice in overdue_invoices(clinic)
            if invoice.overdue_days >= (clinic.dunning_days or 0)
            and (invoice.overdue_days - (clinic.dunning_days or 0)) % 7 == 0
        ]
        if not overdue:
            continue
        summary["clinics"] += 1
        for invoice in overdue:
            title = _("[%(clinique)s] Relance — facture %(reference)s") % {
                "clinique": clinic.name,
                "reference": invoice.reference,
            }
            body = (
                _("Bonjour %(patient)s,")
                + "\n\n"
                + _(
                    "Votre facture %(reference)s de %(montant)s, échue le %(echeance)s, "
                    "reste impayée (%(reste)s dû)."
                )
                % {
                    "patient": invoice.patient.display_name,
                    "reference": invoice.reference,
                    "montant": f"{invoice.total:.2f}",
                    "echeance": invoice.due_at.strftime("%d/%m/%Y"),
                    "reste": f"{invoice.amount_due:.2f}",
                }
                + "\n\n"
                + _("Merci de vous rapprocher de l'accueil.")
                + "\n"
                + clinic.name
            )
            for channel in (
                Notification.Channel.WHATSAPP,
                Notification.Channel.EMAIL,
            ):
                notice = queue_notification(
                    clinic=clinic,
                    patient=invoice.patient,
                    appointment=None,
                    channel=channel,
                    category=Notification.Category.SYSTEM,
                    title=title,
                    body=body,
                )
                if notice is not None:
                    summary["patients_relanced"] += 1
                    break  # un seul canal patient par facture et par passage

        for membership in (
            clinic.memberships.filter(is_active=True)
            .filter(role__in=(Membership.Role.ADMIN, Membership.Role.ACCOUNTANT))
            .select_related("user")
        ):
            notice = Notification(
                clinic=clinic,
                recipient=membership.user,
                channel=Notification.Channel.IN_APP,
                category=Notification.Category.SYSTEM,
                title=_("%(count)s factures impayées à relancer")
                % {"count": len(overdue)},
                body="\n".join(
                    _(
                        "%(reference)s — %(patient)s — %(reste)s dû (échue depuis %(jours)s j)"
                    )
                    % {
                        "reference": invoice.reference,
                        "patient": invoice.patient.display_name,
                        "reste": f"{invoice.amount_due:.2f}",
                        "jours": invoice.overdue_days,
                    }
                    for invoice in overdue[:15]
                ),
            )
            notice.full_clean()
            notice.save()
            summary["notices"] += 1

    logger.info("Relances d'impayés : %s", summary)
    return summary
