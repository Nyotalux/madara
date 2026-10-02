"""Tâches de fond d'utilité transverse."""

from __future__ import annotations

import logging

from celery import shared_task
from django.utils import timezone

logger = logging.getLogger(__name__)


@shared_task(name="common.tasks.nightly_maintenance")
def nightly_maintenance():
    """Maintenance nocturne : purge des traces d'accès et des jetons expirés.

    Les tâches spécifiques (partages de dossier expirés, notifications en échec)
    sont ajoutées aux jalons suivants.
    """
    summary = {"executed_at": timezone.now().isoformat()}

    from apps.medical_records.models import RecordAccessLog

    cutoff = timezone.now() - timezone.timedelta(days=365)
    deleted, _ = RecordAccessLog.all_objects.filter(created_at__lt=cutoff).delete()
    summary["access_logs_purged"] = deleted

    logger.info("Maintenance nocturne terminée : %s", summary)
    return summary
