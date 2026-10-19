"""Canaux d'envoi : e-mail et WhatsApp (fournisseur configurable).

Chaque fonction renvoie un couple ``(succès, référence ou erreur)`` afin que
``apps.notifications.services`` journalise l'issue sans lever d'exception.
Le fournisseur WhatsApp est choisi par ``NOTIFICATIONS["WHATSAPP_PROVIDER"]`` :
``console`` (journal local, aucun envoi réel), ``meta`` (WhatsApp Cloud API) ou
``twilio`` (API Messages).
"""

from __future__ import annotations

import base64
import json
import logging
import urllib.error
import urllib.parse
import urllib.request

from django.conf import settings
from django.core.mail import send_mail
from django.utils.translation import gettext_lazy as _

logger = logging.getLogger(__name__)

#: Durée maximale des appels HTTP entrants (WhatsApp), en secondes.
_HTTP_TIMEOUT = 10


def send_email(*, subject: str, body: str, to_email: str) -> tuple[bool, str]:
    """Envoie un e-mail texte via le backend Django configuré."""
    if not to_email:
        return False, _("Adresse e-mail absente.")

    subject = (subject or "").strip() or _("Notification Madara")
    body = (body or "").strip()
    from_name = settings.NOTIFICATIONS.get("DEFAULT_FROM_NAME", "Madara")
    from_email = (
        settings.NOTIFICATIONS.get("DEFAULT_FROM_EMAIL") or settings.DEFAULT_FROM_EMAIL
    )
    try:
        count = send_mail(
            subject=subject,
            message=body,
            from_email=f"{from_name} <{from_email}>",
            recipient_list=[to_email],
            fail_silently=False,
        )
    except Exception as exc:  # pragma: no cover - depends on backend
        logger.warning("E-mail non envoyé à %s : %s", to_email, exc)
        return False, str(exc)
    if count == 0:
        return False, _("Le backend e-mail n'a rien envoyé.")
    return True, to_email


def send_whatsapp(*, body: str, phone: str) -> tuple[bool, str]:
    """Envoie un message WhatsApp selon le fournisseur configuré."""
    if not phone:
        return False, _("Numéro de téléphone absent.")

    body = (body or "").strip()
    provider = settings.NOTIFICATIONS.get("WHATSAPP_PROVIDER", "console")
    if provider == "meta":
        return _whatsapp_meta(body, phone)
    if provider == "twilio":
        return _whatsapp_twilio(body, phone)
    # console (« boîte de développement ») : simple journal.
    logger.info("WhatsApp (console) → %s : %s", phone, body[:200])
    return True, f"console:{phone}"


# ---------------------------------------------------------------------------
# Web Push (navigateur du back-office)
# ---------------------------------------------------------------------------


def vapid_public_key() -> str:
    """Clé publique VAPID dérivée de la clé privée configurée ("" si absente).

    La clé privée est stockée en base64url sans bourrage (DER PKCS8), format
    produit par ``python manage.py generate_vapid_keys``. La dérivation est
    mise en cache : la configuration ne change pas en cours de vie du service.
    """
    global _vapid_cache
    private = settings.NOTIFICATIONS.get("WEBPUSH_PRIVATE_KEY", "")
    if not private:
        return ""
    cached_key, cached_public = _vapid_cache
    if cached_key == private:
        return cached_public
    try:
        from base64 import urlsafe_b64decode, urlsafe_b64encode

        from cryptography.hazmat.primitives import serialization

        key = serialization.load_der_private_key(
            urlsafe_b64decode(private + "=" * (-len(private) % 4)), password=None
        )
        point = key.public_key().public_bytes(
            serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
        )
        public = urlsafe_b64encode(point).rstrip(b"=").decode()
    except Exception as exc:
        logger.warning("Clé VAPID illisible : %s", exc)
        public = ""
    _vapid_cache = (private, public)
    return public


#: Dernier couple (clé privée, clé publique dérivée) calculé.
_vapid_cache = ("", "")


def send_webpush(
    *,
    endpoint: str,
    p256dh: str,
    auth: str,
    title: str,
    body: str,
    url: str = "",
) -> tuple[bool, str]:
    """Pousse une notification sur un abonnement navigateur.

    Renvoie ``(succès, référence ou erreur)`` ; une erreur ``gone`` signale un
    endpoint mort (l'appelant désactive alors la souscription).
    """
    conf = settings.NOTIFICATIONS
    private_key = conf.get("WEBPUSH_PRIVATE_KEY", "")
    if not private_key:
        return False, "vapid-non-configure"
    if not (endpoint and p256dh and auth):
        return False, "abonnement-incomplet"

    payload = json.dumps(
        {"title": title or "Madara", "body": body or "", "url": url or "/notifications/"}
    )
    claims = {
        "sub": f"mailto:{conf.get('WEBPUSH_CONTACT') or 'admin@madara.ma'}",
    }
    try:
        from pywebpush import WebPushException, webpush

        webpush(
            {"endpoint": endpoint, "keys": {"p256dh": p256dh, "auth": auth}},
            data=payload,
            vapid_private_key=private_key,
            vapid_claims=claims,
            ttl=4 * 3600,
            timeout=10,
        )
    except WebPushException as exc:
        status = getattr(getattr(exc, "response", None), "status_code", None)
        detail = f"push-http:{status}" if status else str(exc.__cause__ or exc)[:500]
        logger.warning("Web Push refusé (%s) : %s", detail, endpoint[:80])
        return False, detail
    except Exception as exc:
        logger.warning("Web Push impossible : %s", exc)
        return False, str(exc)[:500]
    return True, "endpoint-accepte"


def _whatsapp_meta(body: str, phone: str) -> tuple[bool, str]:
    """WhatsApp Cloud API (Meta Graph)."""
    conf = settings.NOTIFICATIONS
    token = conf.get("META_WHATSAPP_TOKEN", "")
    phone_id = conf.get("META_WHATSAPP_PHONE_ID", "")
    version = conf.get("META_WHATSAPP_API_VERSION", "v21.0")
    if not token or not phone_id:
        return False, _("WhatsApp Meta non configuré (jeton ou ID de téléphone).")

    url = f"https://graph.facebook.com/{version}/{phone_id}/messages"
    payload = json.dumps(
        {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": phone,
            "type": "text",
            "text": {"preview_url": False, "body": body[:4096]},
        }
    ).encode()
    request = urllib.request.Request(
        url,
        data=payload,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=_HTTP_TIMEOUT) as response:
            data = json.loads(response.read().decode() or "{}")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")[:2000] if exc.fp else str(exc)
        logger.warning("WhatsApp Meta : HTTP %s — %s", exc.code, detail)
        return False, detail
    except Exception as exc:
        logger.warning("WhatsApp Meta injoignable : %s", exc)
        return False, str(exc)
    message_id = (data.get("messages") or [{}])[0].get("id", "")
    return True, message_id or f"meta:{phone}"


def _whatsapp_twilio(body: str, phone: str) -> tuple[bool, str]:
    """API Messages Twilio (authentification HTTP basique)."""
    conf = settings.NOTIFICATIONS
    sid = conf.get("TWILIO_ACCOUNT_SID", "")
    token = conf.get("TWILIO_AUTH_TOKEN", "")
    sender = conf.get("TWILIO_WHATSAPP_FROM", "")
    if not sid or not token or not sender:
        return False, _("Twilio non configuré (identifiants ou expéditeur).")

    url = f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json"
    data = urllib.parse.urlencode(
        {"To": f"whatsapp:{phone}", "From": f"whatsapp:{sender}", "Body": body[:4096]}
    ).encode()
    request = urllib.request.Request(
        url,
        data=data,
        headers={
            "Authorization": "Basic "
            + base64.b64encode(f"{sid}:{token}".encode()).decode(),
            "Content-Type": "application/x-www-form-urlencoded",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=_HTTP_TIMEOUT) as response:
            payload = json.loads(response.read().decode() or "{}")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")[:2000] if exc.fp else str(exc)
        logger.warning("WhatsApp Twilio : HTTP %s — %s", exc.code, detail)
        return False, detail
    except Exception as exc:
        logger.warning("Twilio injoignable : %s", exc)
        return False, str(exc)
    return True, f"twilio:{payload.get('sid', phone)}"
