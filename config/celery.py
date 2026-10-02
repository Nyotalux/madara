"""Application Celery du projet (notifications planifiees, rapports)."""

import os

from celery import Celery
from celery.schedules import crontab

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")

app = Celery("madara")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()

# ---------------------------------------------------------------------------
# Planification (beat)
# ---------------------------------------------------------------------------

app.conf.beat_schedule = {
    # Envoi des notifications dont l'heure est arrivée (toutes les 2 min).
    "dispatch-pending-notifications": {
        "task": "notifications.dispatch_pending",
        "schedule": crontab(minute="*/2"),
    },
    # Agenda du jour envoye chaque matin a 07:00.
    "daily-agenda-digest": {
        "task": "appointments.tasks.send_daily_agenda",
        "schedule": crontab(hour=7, minute=0),
    },
    # Rappel une heure avant le rendez-vous (le patient a ete confirme).
    "appointment-hourly-reminder": {
        "task": "appointments.tasks.send_upcoming_reminders",
        "schedule": crontab(minute="*/10"),
    },
    # Rapport de recette du jour + alertes stock bas.
    "daily-clinic-report": {
        "task": "dashboard.tasks.send_daily_report",
        "schedule": crontab(hour=20, minute=0),
    },
    # Maintenance nocturne.
    "nightly-maintenance": {
        "task": "common.tasks.nightly_maintenance",
        "schedule": crontab(hour=3, minute=0),
    },
    # Rappel de relance des factures impayees (tous les jours a 09:00).
    "dunning-invoices": {
        "task": "billing.tasks.send_overdue_invoice_reminders",
        "schedule": crontab(hour=9, minute=0),
    },
}


@app.task(bind=True, ignore_result=True)
def debug_task(self):
    """Tache de diagnostic : affiche la configuration Celery."""
    return {"request": str(self.request)}