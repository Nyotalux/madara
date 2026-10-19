"""Notifications et rappels (jalon 5).

Une notification est un message sortant de la clinique : rappel de rendez-vous
au patient (WhatsApp, e-mail), alerte in-app au personnel (back-office) ou e-mail
de synthèse (agenda du jour). Le rendu concret dépend du canal :

- ``EMAIL`` — Django ``send_mail`` (backend configurable par ``EMAIL_BACKEND``) ;
- ``WHATSAPP`` — fournisseur choisi par ``NOTIFICATIONS["WHATSAPP_PROVIDER"]``
  (``console`` : simple journal, ``meta`` : WhatsApp Cloud API, ``twilio``) ;
- ``IN_APP`` — aucun envoi externe : la ligne suffit, elle est lue dans le
  back-office puis marquée « lue » (compteur de la barre de navigation).

La mise en file d'attente et l'envoi passent par ``apps.notifications.services`` ;
les tâches Celery (cadenas dans ``config/celery.py``) dans
``apps.notifications.tasks``.
"""

from __future__ import annotations

from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.common.models import BaseModel, TimeStampedModel, UUIDModel


class Notification(BaseModel):
    """Message sortant, en file d'attente puis envoyé par canal."""

    class Channel(models.TextChoices):
        EMAIL = "EMAIL", _("E-mail")
        WHATSAPP = "WHATSAPP", _("WhatsApp")
        IN_APP = "IN_APP", _("Back-office")

    class Category(models.TextChoices):
        APPOINTMENT_REMINDER = "APPOINTMENT_REMINDER", _("Rappel de rendez-vous")
        AGENDA_DIGEST = "AGENDA_DIGEST", _("Agenda du jour")
        SYSTEM = "SYSTEM", _("Information")

    class Status(models.TextChoices):
        PENDING = "PENDING", _("En attente")
        SENT = "SENT", _("Envoyée")
        FAILED = "FAILED", _("En échec")
        CANCELLED = "CANCELLED", _("Annulée")

    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name=_("destinataire"),
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="notifications",
        help_text=_("Membre du personnel (canal back-office et e-mails internes)."),
    )
    patient = models.ForeignKey(
        "patients.Patient",
        verbose_name=_("patient"),
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="notifications",
        help_text=_("Rappels envoyés au patient sur son téléphone ou son e-mail."),
    )
    appointment = models.ForeignKey(
        "appointments.Appointment",
        verbose_name=_("rendez-vous"),
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="notifications",
    )
    channel = models.CharField(_("canal"), max_length=12, choices=Channel.choices)
    category = models.CharField(
        _("catégorie"),
        max_length=24,
        choices=Category.choices,
        default=Category.SYSTEM,
    )
    status = models.CharField(
        _("état"), max_length=12, choices=Status.choices, default=Status.PENDING
    )
    title = models.CharField(_("objet"), max_length=140)
    body = models.TextField(_("message"))
    # Instantanés au moment de la mise en file d'attente : le destinataire peut
    # changer de numéro ou d'e-mail après coup, le message envoyé reste tracé.
    phone = models.CharField(_("téléphone"), max_length=20, blank=True)
    email = models.EmailField(_("e-mail"), blank=True)
    scheduled_for = models.DateTimeField(
        _("prévue le"),
        null=True,
        blank=True,
        help_text=_(
            "Heure d'envoi souhaitée ; les notifications déjà dues partent à chaque passage."
        ),
    )
    sent_at = models.DateTimeField(_("envoyée le"), null=True, blank=True)
    read_at = models.DateTimeField(_("lue le"), null=True, blank=True)
    attempts = models.PositiveSmallIntegerField(_("tentatives"), default=0)
    last_error = models.TextField(_("dernière erreur"), blank=True)
    pushed_at = models.DateTimeField(
        _("poussée au navigateur le"),
        null=True,
        blank=True,
        help_text=_("Envoi Web Push réalisé (canal navigateur du back-office)."),
    )

    class Meta:
        verbose_name = _("notification")
        verbose_name_plural = _("notifications")
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["clinic", "recipient", "read_at"]),
            models.Index(fields=["status", "scheduled_for"]),
            models.Index(fields=["clinic", "category", "-created_at"]),
        ]

    def __str__(self):
        who = self.recipient or self.patient or self.phone or self.email or "—"
        return f"[{self.get_channel_display()}] {self.title} → {who}"

    # -- Cycle de vie -----------------------------------------------------

    def mark_sent(self):
        """Passe la notification en « envoyée » et marque le rappel adossé."""
        self.status = self.Status.SENT
        self.sent_at = timezone.now()
        self.save(update_fields=["status", "sent_at", "updated_at"])
        if self.appointment_id and self.category == self.Category.APPOINTMENT_REMINDER:
            self.appointment.reminder_sent_at = self.sent_at
            self.appointment.save(update_fields=["reminder_sent_at", "updated_at"])

    def mark_failed(self, error: str):
        """Conserve la notification pour une future tentative de renvoi."""
        self.status = self.Status.FAILED
        self.last_error = (error or "").strip()[:2000]
        self.save(update_fields=["status", "last_error", "updated_at"])

    def mark_read(self, *, user=None) -> bool:
        """Marque une notification du back-office comme lue.

        Seul le destinataire (ou un compte plateforme) peut le faire.
        """
        if self.recipient_id is None:
            return False
        if user is not None and not getattr(user, "is_platform_staff", False):
            if user.pk != self.recipient_id:
                return False
        if self.read_at is None:
            self.read_at = timezone.now()
            self.save(update_fields=["read_at", "updated_at"])
        return True

    # -- Affichage --------------------------------------------------------

    @property
    def is_unread(self) -> bool:
        return self.read_at is None and self.recipient_id is not None

    @property
    def display_recipient(self) -> str:
        if self.recipient_id:
            return self.recipient.full_name or self.recipient.email
        if self.patient_id:
            return self.patient.display_name
        return self.phone or self.email or "—"

    @property
    def is_due(self) -> bool:
        if self.status != self.Status.PENDING:
            return False
        return self.scheduled_for is None or self.scheduled_for <= timezone.now()


class PushSubscription(UUIDModel, TimeStampedModel):
    """Abonnement Web Push d'un appareil du personnel (jalon 5 bis).

    Un appareil = un navigateur qui s'est inscrit via le service worker
    (``/sw.js``). La même souscription sert toutes les cliniques de
    l'utilisateur : il ne s'agit pas d'une donnée métier cloisonnée.
    Les souscriptions expirées (endpoint 404/410) sont désactivées, jamais
    supprimées, pour garder la trace de l'appareil.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        verbose_name=_("utilisateur"),
        on_delete=models.CASCADE,
        related_name="push_subscriptions",
    )
    endpoint = models.CharField(
        _("point de terminaison"),
        max_length=500,
        unique=True,
        help_text=_("URL du service push du navigateur (Jetpack/FCM, Mozilla…)."),
    )
    p256dh = models.CharField(_("clé p256dh"), max_length=120)
    auth = models.CharField(_("clé d'authentification"), max_length=60)
    user_agent = models.CharField(_("navigateur"), max_length=255, blank=True)
    is_active = models.BooleanField(_("actif"), default=True, db_index=True)
    last_seen = models.DateTimeField(_("dernier envoi"), null=True, blank=True)

    class Meta:
        verbose_name = _("abonnement web push")
        verbose_name_plural = _("abonnements web push")
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.user} — {self.user_agent or self.endpoint[:40]}"
