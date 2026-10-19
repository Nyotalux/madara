"""Tâches de fond des notifications (cadenas dans ``config/celery.py``)."""

from __future__ import annotations

import logging

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(bind=True, name="notifications.dispatch_pending", max_retries=3)
def dispatch_pending(self):
    """Envoie les notifications dont l'heure est venue (toutes les 2 minutes)."""
    from .services import dispatch_pending as _dispatch_pending

    try:
        return _dispatch_pending()
    except Exception as exc:  # pragma: no cover - dépend du broker
        logger.warning("Envoi des notifications en échec : %s", exc)
        raise self.retry(exc=exc, countdown=60) from exc
