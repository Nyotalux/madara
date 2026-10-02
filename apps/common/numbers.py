"""Numérotation séquentielle, téléphones et formats français."""

from __future__ import annotations

import re
from datetime import date, datetime

from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _

# Nettoyage des numéros : on ne garde que les chiffres et un éventuel "+".
_PHONE_RE = re.compile(r"[^\d+]")


# ---------------------------------------------------------------------------
# Téléphones
# ---------------------------------------------------------------------------


def normalize_phone(raw: str | None) -> str:
    """``+212 6 12 34 56 78`` -> ``+212612345678``."""
    if not raw:
        return ""
    cleaned = _PHONE_RE.sub("", str(raw))
    return cleaned if cleaned.startswith("+") else cleaned.lstrip("+")


def mask_phone(raw: str | None) -> str:
    """Masque un numéro pour l'affichage public : ``+212****5678``."""
    phone = normalize_phone(raw)
    if not phone:
        return ""
    visible = 4 if len(phone) > 8 else 2
    return f"{'*' * max(len(phone) - visible - 1, 1)}{phone[-visible:]}"


def format_phone_fr(raw: str | None) -> str:
    """Affichage lisible : ``+212612345678`` -> ``+212 612-345678``."""
    phone = normalize_phone(raw)
    if not phone:
        return ""
    if phone.startswith("+212"):
        national = phone[4:]
        if len(national) in (8, 9):
            return f"+212 {national[:3]}-{national[3:]}"
        return f"+212 {national}" if national else phone
    if phone.startswith("0"):
        return f"{phone[:4]}-{phone[4:]}" if len(phone) == 10 else phone
    if phone.startswith("+") and len(phone) == 10:
        return f"+{phone[1:3]} {phone[3:5]} {phone[5:7]} {phone[7:9]} {phone[9:]}"
    return phone


# ---------------------------------------------------------------------------
# Références séquentielles (n° dossier, n° facture, n° ordonnance...)
# ---------------------------------------------------------------------------


def next_sequence(
    clinic,
    kind: str,
    prefix: str,
    *,
    digits: int = 4,
    period: str | None = None,
) -> str:
    """Réserve et renvoie la prochaine référence d'une clinique.

    Exemples : ``PT-2026-0001``, ``FAC-2026-0007``, ``ORD-2026-0032``.
    Le compteur est incrémenté sous verrou pessimiste : deux factures créées
    en parallèle obtiennent deux numéros distincts.
    """
    from apps.common.models import SequenceCounter

    period = period or f"{timezone.localdate().year}"
    with transaction.atomic():
        counter = (
            SequenceCounter.all_objects.select_for_update()
            .filter(clinic=clinic, kind=kind, period=period)
            .first()
        )
        if counter is None:
            # Deux créations simultanées : celle qui aboutit conserve la ligne.
            counter = SequenceCounter.all_objects.create(
                clinic=clinic,
                kind=kind,
                period=period,
                prefix=prefix,
                digits=digits,
                last_value=1,
            )
            return f"{prefix}-{period}-{1:0{digits}d}"

        next_value = counter.last_value + 1
        SequenceCounter.all_objects.filter(pk=counter.pk).update(last_value=next_value)
        return f"{prefix}-{period}-{next_value:0{digits}d}"


# ---------------------------------------------------------------------------
# Formats d'affichage
# ---------------------------------------------------------------------------


def format_money(amount, currency: str = "MAD") -> str:
    """``1250`` -> ``1 250,00 MAD`` (format français)."""
    if amount is None:
        amount = 0
    formatted = f"{float(amount):,.2f}".replace(",", " ").replace(".", ",")
    return f"{formatted} {currency}".strip()


def format_date_fr(value: date | datetime | None) -> str:
    return value.strftime("%d/%m/%Y") if value else ""


def format_time_fr(value: datetime | None) -> str:
    return value.strftime("%H:%M") if value else ""


def format_datetime_fr(value: datetime | None) -> str:
    return value.strftime("%d/%m/%Y à %H:%M") if value else ""


def full_name(first_name: str, last_name: str = "") -> str:
    return f"{last_name} {first_name}".strip() or _("Sans nom")


def initials(first_name: str, last_name: str = "") -> str:
    letters = (first_name[:1] + (last_name[:1] if last_name else "")).upper()
    return letters or "?"


def age_from_birth_date(birth_date: date | None) -> int | None:
    """Age révolu à la date du jour."""
    if not birth_date:
        return None
    today = timezone.localdate()
    years = today.year - birth_date.year
    if (today.month, today.day) < (birth_date.month, birth_date.day):
        years -= 1
    return max(years, 0)


def slugify_fr(value: str) -> str:
    """Slug compatible accents : ``Clinique Médicale Al Amal`` -> ``clinique-medicale-al-amal``."""
    import unicodedata

    normalized = unicodedata.normalize("NFKD", value or "")
    ascii_only = normalized.encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", ascii_only).strip("-").lower()
    return slug or "clinique"
