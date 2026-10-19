"""Web Push navigateur : abonnements, fan-out, service worker (jalon 5 bis)."""

from __future__ import annotations

import base64

import pytest
from django.core.management import call_command
from rest_framework.test import APIClient

from apps.notifications import providers
from apps.notifications.models import Notification, PushSubscription
from apps.notifications.services import dispatch_pending, queue_notification

from .factories import make_staff  # noqa: F401 (fabriques partagées)

pytestmark = pytest.mark.django_db

ENDPOINT = "https://fcm.googleapis.com/fcm/send/test-sub"


def push_subscription(user, *, endpoint=ENDPOINT, is_active=True, **kwargs):
    defaults = {
        "user": user,
        "endpoint": endpoint,
        "p256dh": base64.urlsafe_b64encode(b"p256dh-example").rstrip(b"=").decode(),
        "auth": base64.urlsafe_b64encode(b"auth-example").rstrip(b"=").decode(),
        "user_agent": "Mozilla/5.0 (test)",
        "is_active": is_active,
    }
    defaults.update(kwargs)
    return PushSubscription.objects.create(**defaults)


@pytest.fixture
def vapid_key(monkeypatch):
    """Configure une clé privée VAPID « réelle » pour les tests du module."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from django.conf import settings

    key = ec.generate_private_key(ec.SECP256R1())
    private = key.private_bytes(
        serialization.Encoding.DER,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    value = base64.urlsafe_b64encode(private).rstrip(b"=").decode()
    monkeypatch.setitem(settings.NOTIFICATIONS, "WEBPUSH_PRIVATE_KEY", value)
    monkeypatch.setattr(providers, "_vapid_cache", ("", ""))
    yield value
    monkeypatch.setattr(providers, "_vapid_cache", ("", ""))


# ---------------------------------------------------------------------------
# Clés VAPID et fournisseur
# ---------------------------------------------------------------------------


class TestKeys:
    def test_cle_privee_absente_renvoie_chaine_vide(self):
        assert providers.vapid_public_key() == ""

    def test_cle_publique_derivee_de_la_cle_privee(self, vapid_key):
        public = providers.vapid_public_key()
        assert public and len(public) >= 80
        # Cache : deuxième appel, même valeur.
        assert providers.vapid_public_key() == public

    def test_cle_privee_illisible_ne_plante_pas(self, monkeypatch):
        from django.conf import settings

        monkeypatch.setitem(settings.NOTIFICATIONS, "WEBPUSH_PRIVATE_KEY", "pas-une-cle")
        monkeypatch.setattr(providers, "_vapid_cache", ("", ""))
        assert providers.vapid_public_key() == ""

    def test_referentiel_demo_est_linstantane(self, capsys):
        call_command("generate_vapid_keys")
        out = capsys.readouterr().out
        assert "WEBPUSH_VAPID_PRIVATE_KEY=" in out


class TestSendWebpush:
    def test_sans_cle_vapid_refuse_sans_reseau(self):
        ok, detail = providers.send_webpush(
            endpoint=ENDPOINT,
            p256dh="k",
            auth="a",
            title="Bonjour",
            body="…",
        )
        assert not ok
        assert detail == "vapid-non-configure"

    def test_abonnement_incomplet_refuse(self, vapid_key):
        ok, detail = providers.send_webpush(
            endpoint="", p256dh="k", auth="a", title="t", body="b"
        )
        assert not ok and detail == "abonnement-incomplet"


# ---------------------------------------------------------------------------
# Abonnement via l'API et service worker
# ---------------------------------------------------------------------------


def api_client(user) -> APIClient:
    api = APIClient()
    api.force_authenticate(user=user)
    return api


class TestSubscriptionApi:
    def test_cle_publique_refusee_sans_compte(self):
        response = APIClient().get("/api/v1/webpush/cle/")
        assert response.status_code in (401, 403)

    def test_cle_publique_exposee_au_navigateur(self, doctor, vapid_key):
        response = api_client(doctor).get("/api/v1/webpush/cle/")
        assert response.status_code == 200
        assert response.json()["public_key"] == providers.vapid_public_key()

    def test_inscription_et_mise_a_jour_sans_doublon(self, clinic, doctor):
        api = api_client(doctor)
        payload = {
            "endpoint": ENDPOINT,
            "keys": {"p256dh": "dhs-cle", "auth": "auth-cle"},
            "user_agent": "Firefox",
        }
        first = api.post("/api/v1/webpush/", payload, format="json")
        assert first.status_code == 200
        assert PushSubscription.objects.count() == 1

        # Re-inscription du même appareil : mise à jour, pas de doublon.
        second = api.post(
            "/api/v1/webpush/", {**payload, "user_agent": "Chrome"}, format="json"
        )
        assert second.status_code == 200
        assert PushSubscription.objects.count() == 1
        row = PushSubscription.objects.get()
        assert row.user == doctor
        assert row.p256dh == "dhs-cle" and row.user_agent == "Chrome"

    def test_retrait_de_l_appareil(self, clinic, doctor):
        push_subscription(doctor)
        api = api_client(doctor)
        response = api.delete("/api/v1/webpush/", {"endpoint": ENDPOINT}, format="json")
        assert response.status_code == 200
        assert not PushSubscription.objects.exists()

    def test_retirer_l_abonnement_d_autrui_est_impossible(self, clinic, doctor, nurse):
        push_subscription(doctor)
        push_subscription(nurse, endpoint=ENDPOINT + "-nurse")
        response = api_client(nurse).delete(
            "/api/v1/webpush/", {"endpoint": ENDPOINT}, format="json"
        )
        assert response.status_code == 200
        # La souscription du médecin est intacte (la suppression est filtrée
        # par utilisateur).
        assert PushSubscription.objects.filter(user=doctor).exists()


class TestServiceWorker:
    def test_serve_le_sw_a_la_racine(self, client):
        response = client.get("/sw.js")
        assert response.status_code == 200
        assert response["Content-Type"].startswith("application/javascript")
        assert "Service-Worker-Allowed" in response

    def test_la_cle_vapid_est_injectee_dans_le_javascript(self, client, vapid_key):
        source = client.get("/sw.js").content.decode()
        assert f'"{providers.vapid_public_key()}"' in source


# ---------------------------------------------------------------------------
# Fan-out au dispatch
# ---------------------------------------------------------------------------


class TestFanout:
    def test_le_dispatch_pousse_vers_les_appareils(self, clinic, doctor, monkeypatch):
        push_subscription(doctor)
        push_subscription(doctor, endpoint=ENDPOINT + "-2")
        push_subscription(doctor, endpoint=ENDPOINT + "-morte", is_active=False)
        pushed = []

        def fake_send(**kwargs):
            pushed.append(kwargs["endpoint"])
            return True, "endpoint-accepte"

        monkeypatch.setattr(providers, "send_webpush", fake_send)
        queue_notification(
            clinic=clinic,
            recipient=doctor,
            channel=Notification.Channel.IN_APP,
            title="Rappel",
            body="Patient à venir.",
        )
        summary = dispatch_pending()
        assert summary["sent"] == 1
        assert summary["webpush"] == 2  # les souscriptions inactives sont ignorées
        assert set(pushed) == {ENDPOINT, ENDPOINT + "-2"}
        notice = Notification.objects.get()
        assert notice.pushed_at is not None
        assert notice.status == Notification.Status.SENT

    def test_un_endpoint_mort_est_desactive(self, clinic, doctor, monkeypatch):
        subscription = push_subscription(doctor)

        def fake_send(**kwargs):
            return False, "push-http:410"

        monkeypatch.setattr(providers, "send_webpush", fake_send)
        queue_notification(
            clinic=clinic,
            recipient=doctor,
            channel=Notification.Channel.IN_APP,
            title="Rappel",
            body="…",
        )
        dispatch_pending()
        subscription.refresh_from_db()
        assert subscription.is_active is False

    def test_sans_appareil_le_dispatch_reste_inchange(self, clinic, doctor, monkeypatch):
        def fake_send(**kwargs):  # ne doit jamais être appelé
            raise AssertionError("aucun appel attendu")

        monkeypatch.setattr(providers, "send_webpush", fake_send)
        queue_notification(
            clinic=clinic,
            recipient=doctor,
            channel=Notification.Channel.IN_APP,
            title="Rappel",
            body="…",
        )
        summary = dispatch_pending()
        assert summary["sent"] == 1 and summary["webpush"] == 0
