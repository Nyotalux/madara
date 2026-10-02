"""Vues web (interface du personnel)."""

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.forms import PasswordChangeForm
from django.contrib.auth.views import LoginView, LogoutView
from django.db.models import Count
from django.http import HttpResponseRedirect
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.common.decorators import require_clinic, require_roles

from .forms import LoginForm, MembershipForm, UserProfileForm
from .models import Clinic, Membership, OpeningHour

WEEKDAY_CHOICES = OpeningHour._meta.get_field("weekday").choices


# ---------------------------------------------------------------------------
# Authentification
# ---------------------------------------------------------------------------


class MadaraLoginView(LoginView):
    """Connexion e-mail + mot de passe, avec mémorisation de la clinique."""

    template_name = "accounts/login.html"
    authentication_form = LoginForm
    redirect_authenticated_user = True

    def form_valid(self, form):
        response = super().form_valid(form)
        requested = self.request.POST.get("clinic")
        clinic = None
        if requested:
            clinic = self.request.user.clinics.filter(pk=requested).first()
        else:
            clinic = self.request.user.primary_membership
        if clinic is not None:
            self.request.session["madara_clinic_id"] = clinic.pk
        next_url = self.get_redirect_url()
        return HttpResponseRedirect(next_url or reverse("web:dashboard"))

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        user = self.request.user
        context["clinics"] = (
            user.clinics.all() if user.is_authenticated else []
        )
        return context


class MadaraLogoutView(LogoutView):
    def get_success_url(self):
        return reverse("web:login")


# ---------------------------------------------------------------------------
# Cliniques
# ---------------------------------------------------------------------------


@login_required
def switch_clinic(request):
    """Change la clinique courante (session)."""
    if request.method != "POST":
        return redirect("web:dashboard")

    clinic_id = request.POST.get("clinic")
    clinic = get_object_or_404(request.user.clinics, pk=clinic_id)
    request.session["madara_clinic_id"] = clinic.pk
    messages.success(request, _("Clinique active : %(clinic)s") % {"clinic": clinic})
    return redirect(request.POST.get("next") or reverse("web:dashboard"))


@login_required
def clinic_detail(request, slug):
    clinic = get_object_or_404(Clinic, slug=slug, is_active=True)
    staff = clinic.memberships.filter(is_active=True).select_related("user")
    return render(
        request,
        "accounts/clinic_detail.html",
        {
            "clinic": clinic,
            "staff": staff,
            "doctors": staff.filter(role=Membership.Role.DOCTOR),
        },
    )


# ---------------------------------------------------------------------------
# Profil
# ---------------------------------------------------------------------------


@login_required
def profile(request):
    if request.method == "POST":
        form = UserProfileForm(request.POST, request.FILES, instance=request.user)
        if form.is_valid():
            form.save()
            messages.success(request, _("Profil mis à jour."))
            return redirect("web:profile")
    else:
        form = UserProfileForm(instance=request.user)

    password_form = PasswordChangeForm(user=request.user)

    return render(
        request,
        "accounts/profile.html",
        {"form": form, "password_form": password_form},
    )


@login_required
def change_password(request):
    form = PasswordChangeForm(user=request.user, data=request.POST)
    if form.is_valid():
        user = form.save()
        update_session_auth_hash(request, user)
        messages.success(request, _("Mot de passe modifié."))
        return redirect("web:profile")

    return render(
        request,
        "accounts/profile.html",
        {
            "form": UserProfileForm(instance=request.user),
            "password_form": form,
        },
    )


# ---------------------------------------------------------------------------
# Personnel
# ---------------------------------------------------------------------------


@login_required
@require_clinic
def staff_list(request):
    clinic = request.clinic
    memberships = (
        Membership.all_objects.filter(clinic=clinic)
        .select_related("user", "practitioner_profile")
        .order_by("is_active", "user__last_name")
    )
    role_filter = request.GET.get("role")
    if role_filter:
        memberships = memberships.filter(role=role_filter.upper())

    counts = (
        Membership.objects.filter(clinic=clinic)
        .values("role")
        .annotate(total=Count("id"))
    )
    count_by_role = {row["role"]: row["total"] for row in counts}
    role_rows = [
        (value, label, count_by_role.get(value, 0)) for value, label in Membership.Role.choices
    ]

    return render(
        request,
        "accounts/staff_list.html",
        {
            "memberships": memberships,
            "role_filter": role_filter,
            "role_counts": count_by_role,
            "role_rows": role_rows,
        },
    )


@login_required
@require_clinic
@require_roles(Membership.Role.ADMIN)
def staff_create(request):
    form = MembershipForm(request.POST or None, clinic=request.clinic)
    if request.method == "POST" and form.is_valid():
        membership = form.save()
        messages.success(
            request,
            _("Membre du personnel enregistré : %(name)s")
            % {"name": membership.user},
        )
        return redirect("web:staff-detail", pk=membership.pk)

    return render(
        request,
        "accounts/staff_form.html",
        {"form": form, "membership": None, "title": _("Nouveau membre du personnel")},
    )


@login_required
@require_clinic
@require_roles(Membership.Role.ADMIN)
def staff_edit(request, pk):
    membership = get_object_or_404(
        Membership.all_objects.select_related("user"), pk=pk, clinic=request.clinic
    )
    initial = {
        "email": membership.user.email,
        "first_name": membership.user.first_name,
        "last_name": membership.user.last_name,
    }
    form = MembershipForm(
        request.POST or None,
        instance=membership,
        initial=initial,
        clinic=request.clinic,
    )
    if request.method == "POST" and form.is_valid():
        membership = form.save()
        messages.success(request, _("Fiche du personnel mise à jour."))
        return redirect("web:staff-detail", pk=membership.pk)

    return render(
        request,
        "accounts/staff_form.html",
        {
            "form": form,
            "membership": membership,
            "title": f"{membership.user} — modifier",
        },
    )


@login_required
@require_clinic
@require_roles(Membership.Role.ADMIN)
def staff_detail(request, pk):
    membership = get_object_or_404(
        Membership.all_objects.select_related("user", "practitioner_profile"),
        pk=pk,
        clinic=request.clinic,
    )
    opening_hours = membership.opening_hours.all()
    blocked = membership.blocked_slots.filter(end_at__gte=timezone.now()).order_by(
        "start_at"
    )

    return render(
        request,
        "accounts/staff_detail.html",
        {
            "membership": membership,
            "opening_hours": opening_hours,
            "blocked_slots": blocked,
        },
    )