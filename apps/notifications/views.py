"""Vues web des notifications (jalon 5) : casier, lecture, service worker.

Chaque utilisateur ne voit que ses propres messages de la clinique courante :
les rappels destinés aux patients (WhatsApp, e-mail) et les autres membres ne
sont jamais visibles ici (cf. l'administration pour la trace complète).
"""

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.common.decorators import require_clinic
from apps.common.pagination import paginate

from .models import Notification
from .providers import vapid_public_key

#: Raccourcis de filtre : tout, non lues uniquement.
STATUS_FILTERS = ("unread", "read")


@login_required
@require_clinic
def notification_list(request):
    """Casier du personnel : messages in-app de la clinique courante."""
    notices = Notification.objects.filter(
        clinic=request.clinic, recipient=request.user
    ).select_related("recipient", "patient", "appointment")

    statut = request.GET.get("statut") or ""
    if statut == "unread":
        notices = notices.filter(read_at__isnull=True)
    elif statut == "read":
        notices = notices.filter(read_at__isnull=False)
    category = request.GET.get("categorie") or ""
    if category in Notification.Category.values:
        notices = notices.filter(category=category)

    page = paginate(request, notices)
    context = {
        "notifications": page["page_obj"].object_list,
        "statut": statut,
        "category": category,
        "categories": Notification.Category.choices,
        **page,
    }
    if request.headers.get("HX-Request") == "true":
        # Fragment HTMX : seul le tableau est rendu.
        return render(request, "notifications/_table.html", context)
    return render(request, "notifications/list.html", context)


@login_required
@require_clinic
def notification_read(request, pk: int):
    """Marque un message comme lu (le destinataire uniquement)."""
    notice = get_object_or_404(
        Notification.objects.filter(clinic=request.clinic, recipient=request.user),
        pk=pk,
    )
    if not notice.mark_read(user=request.user):
        messages.error(request, _("Seul le destinataire peut lire ce message."))
    if request.headers.get("HX-Request") == "true":
        return render(
            request, "notifications/_notification.html", {"notification": notice}
        )
    return redirect(
        request.POST.get("next")
        or request.META.get("HTTP_REFERER")
        or "web:notification-list"
    )


@login_required
@require_clinic
def notification_mark_all_read(request):
    """Remise à zéro du badge : tous les messages sont marqués lus."""
    updated = Notification.objects.filter(
        clinic=request.clinic, recipient=request.user, read_at__isnull=True
    ).update(read_at=timezone.now())
    messages.success(
        request,
        _("%(count)s notifications marquées comme lues.") % {"count": updated}
        if updated
        else _("Aucune notification non lue."),
    )
    return redirect("web:notification-list")


# ---------------------------------------------------------------------------
# Service worker (Web Push)
# ---------------------------------------------------------------------------


def service_worker(request):
    """Sert ``/sw.js`` à la racine (portée = tout le site).

    Un service worker a la portée de son URL : servi depuis ``/static/``, il
    ne contrôlerait que ``/static/``. D'où cette vue dédiée, à la racine,
    qui rend le gabarit JS et expose la clé publique VAPID.
    """
    response = render(
        request,
        "notifications/sw.js",
        {"vapid_public_key": vapid_public_key()},
        content_type="application/javascript",
    )
    response["Service-Worker-Allowed"] = "/"
    return response
