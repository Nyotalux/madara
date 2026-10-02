"""Formulaires web : connexion avec choix de clinique, profil, personnel."""

from __future__ import annotations

from django import forms
from django.contrib.auth.forms import AuthenticationForm
from django.core.exceptions import ValidationError

from .models import Membership, User


class ClinicSelectionForm(forms.Form):
    """Sélection de clinique à la connexion (obligatoire si plusieurs)."""

    clinic = forms.ModelChoiceField(
        queryset=None,
        required=False,
        label="Clinique",
        widget=forms.Select(attrs={"class": "form-select"}),
        help_text="Laissez vide pour utiliser votre clinique par défaut.",
    )

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        if user is not None and user.is_authenticated:
            self.fields["clinic"].queryset = user.clinics
        else:
            self.fields["clinic"].queryset = User.objects.none()


class LoginForm(AuthenticationForm):
    """Connexion par e-mail (identifiant par défaut du modèle User)."""

    username = forms.EmailField(
        label="E-mail",
        widget=forms.EmailInput(
            attrs={
                "class": "form-control",
                "autofocus": True,
                "placeholder": "prenom.nom@clinique.ma",
            }
        ),
    )
    password = forms.CharField(
        label="Mot de passe",
        strip=False,
        widget=forms.PasswordInput(
            attrs={"class": "form-control", "placeholder": "••••••••"}
        ),
    )

    error_messages = {
        "invalid_login": "E-mail ou mot de passe incorrect.",
        "inactive": "Ce compte est désactivé.",
    }

    def __init__(self, request=None, *args, **kwargs):
        super().__init__(request=request, *args, **kwargs)
        self.fields["username"].label = "E-mail"

    def clean_username(self):
        value = self.cleaned_data["username"]
        if "@" in value:
            return value.strip().lower()
        # L'utilisateur tape le nom d'utilisateur Django : on convertit en e-mail.
        user = User.objects.filter(username__iexact=value).first()
        if user is None:
            raise ValidationError("E-mail ou mot de passe incorrect.")
        return user.email


class UserProfileForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ["first_name", "last_name", "email", "phone", "locale", "avatar"]
        widgets = {
            "first_name": forms.TextInput(attrs={"class": "form-control"}),
            "last_name": forms.TextInput(attrs={"class": "form-control"}),
            "email": forms.EmailInput(attrs={"class": "form-control"}),
            "phone": forms.TextInput(attrs={"class": "form-control"}),
            "locale": forms.Select(attrs={"class": "form-select"}),
            "avatar": forms.ClearableFileInput(attrs={"class": "form-control"}),
        }


class MembershipForm(forms.ModelForm):
    """Création / modification d'un membre du personnel."""

    email = forms.EmailField(label="E-mail", required=True)
    first_name = forms.CharField(label="Prénom", required=False)
    last_name = forms.CharField(label="Nom", required=True)
    password = forms.CharField(
        label="Mot de passe",
        required=False,
        help_text="Laissez vide pour conserver le mot de passe existant.",
        widget=forms.PasswordInput(attrs={"class": "form-control"}),
    )

    class Meta:
        model = Membership
        fields = [
            "role",
            "employee_number",
            "hire_date",
            "contract_end_date",
            "base_salary",
            "notes",
        ]
        widgets = {
            "role": forms.Select(attrs={"class": "form-select"}),
            "employee_number": forms.TextInput(attrs={"class": "form-control"}),
            "hire_date": forms.DateInput(
                attrs={"class": "form-control", "type": "date"}
            ),
            "contract_end_date": forms.DateInput(
                attrs={"class": "form-control", "type": "date"}
            ),
            "base_salary": forms.NumberInput(
                attrs={"class": "form-control", "step": "0.01"}
            ),
            "notes": forms.Textarea(attrs={"class": "form-control", "rows": 3}),
        }

    def __init__(self, *args, clinic=None, **kwargs):
        self.clinic = clinic
        super().__init__(*args, **kwargs)
        if clinic is not None:
            self.fields["employee_number"].help_text = (
                f"Unique au sein de {clinic.name}."
            )

    def clean(self):
        cleaned = super().clean()
        email = (cleaned.get("email") or "").strip().lower()
        instance = self.instance
        if email:
            user_qs = User.objects.filter(email__iexact=email)
            if instance.pk:
                user_qs = user_qs.exclude(user=instance.user)
            if user_qs.exists():
                self.add_error("email", "Un compte utilise déjà cette adresse.")
        return cleaned

    def save(self, commit=True):
        membership = super().save(commit=False)
        if self.clinic is not None:
            membership.clinic = self.clinic

        email = self.cleaned_data["email"].strip().lower()
        if not membership.user_id:
            user = User(email=email)
        else:
            user = membership.user
            user.email = email
        user.first_name = self.cleaned_data.get("first_name", "")
        user.last_name = self.cleaned_data.get("last_name", "")
        if self.cleaned_data.get("password"):
            user.set_password(self.cleaned_data["password"])
        elif not user.pk:
            user.set_unusable_password()
        user.save()
        membership.user = user
        membership.save()
        self.save_m2m()
        return membership