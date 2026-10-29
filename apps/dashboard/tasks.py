"""Tâches de fond du tableau de bord (cadenas dans ``config/celery.py``)."""

from __future__ import annotations

import logging
from decimal import Decimal

from celery import shared_task
from django.db.models import Sum
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

logger = logging.getLogger(__name__)


@shared_task(name="dashboard.tasks.send_daily_report")
def send_daily_report():
    """Rapport quotidien de recette et alertes (tous les jours à 20:00).

    Pour chaque clinique : recette encaissée du jour, reste à encaisser
    (impayés échus), alertes stock bas, synthèse in-app à l'administration
    de la clinique. La comptabilité reprend les bilans dans l'écran
    Facturation ; la paie n'entre pas dans ce rapport (cycle mensuel).
    """
    from apps.accounts.models import Clinic
    from apps.billing.models import Payment
    from apps.billing.services import overdue_invoices
    from apps.inventory.services import low_stock_products
    from apps.notifications.models import Notification

    today = timezone.localdate()
    day_start = timezone.make_aware(timezone.datetime(today.year, today.month, today.day))
    day_end = day_start + timezone.timedelta(days=1)

    summary = {"clinics": 0, "notices": 0}
    for clinic in Clinic.all_objects.filter(is_active=True):
        collected_today = Payment.objects.filter(
            clinic_id=clinic.pk, paid_at__gte=day_start, paid_at__lt=day_end
        ).aggregate(total=Sum("amount"))["total"] or Decimal("0")
        overdue = list(overdue_invoices(clinic))
        low_stock = list(low_stock_products(clinic))

        notice = Notification(
            clinic=clinic,
            channel=Notification.Channel.IN_APP,
            category=Notification.Category.SYSTEM,
            title=_("[%(clinique)s] Rapport du %(date)s")
            % {"clinique": clinic.name, "date": today.strftime("%d/%m/%Y")},
            body=_report_text(collected_today, overdue, low_stock),
        )
        notice.full_clean()
        notice.save()
        summary["clinics"] += 1
        summary["notices"] += 1

    logger.info("Rapport quotidien : %s", summary)
    return summary


def _report_text(collected_today: Decimal, overdue, low_stock) -> str:
    lines = [
        _("Recette encaissée du jour : %(montant)s")
        % {"montant": f"{collected_today:.2f}"},
    ]
    if overdue:
        lines.append(
            _("%(count)s facture(s) impayée(s) échue(s), pour %(total)s en attente")
            % {
                "count": len(overdue),
                "total": f"{sum(invoice.amount_due for invoice in overdue):.2f}",
            }
        )
    else:
        lines.append(_("Aucune facture impayée échue."))
    if low_stock:
        names = ", ".join(product.name for product in low_stock[:10])
        lines.append(
            _("%(count)s produit(s) en stock bas : %(noms)s")
            % {"count": len(low_stock), "noms": names}
        )
    else:
        lines.append(_("Aucun produit sous le seuil d'alerte."))
    return "\n".join(lines)
