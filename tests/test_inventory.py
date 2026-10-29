"""Stock : produits, mouvements, alertes, web et API (jalon 6)."""

from __future__ import annotations

from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.urls import reverse
from rest_framework.test import APIClient

from apps.inventory import services
from apps.inventory.models import Product, StockMovement

pytestmark = pytest.mark.django_db

KIND = StockMovement.Kind
CAT = Product.Category


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_product(clinic, **kwargs):
    defaults = {
        "clinic": clinic,
        "name": "Paracétamol 500 mg",
        "category": CAT.MEDICINE,
        "purchase_price": Decimal("10.00"),
        "sale_price": Decimal("15.00"),
        "initial_quantity": Decimal("20"),
    }
    defaults.update(kwargs)
    return services.create_product(**defaults)


def api_client(user) -> APIClient:
    api = APIClient()
    api.force_authenticate(user=user)
    return api


@pytest.fixture
def product(clinic):
    return make_product(clinic)


@pytest.fixture
def other_product(other_clinic):
    return make_product(other_clinic, name="Pansement")


# ---------------------------------------------------------------------------
# Produits, références et montants
# ---------------------------------------------------------------------------


class TestProducts:
    def test_le_produit_porte_une_reference_par_clinique(self, clinic, other_clinic):
        first = make_product(clinic)
        second = make_product(clinic, name="Gants latex")
        other = make_product(other_clinic, name="Seringue 5 ml")
        assert first.reference.startswith("PRD-")
        assert second.reference != first.reference
        assert other.reference.startswith("PRD-")  # numéroter indépendamment

    def test_quantite_initiale_cree_le_mouvement(self, clinic, product):
        assert product.stock_quantity == Decimal("20")
        movement = product.movements.get()
        assert movement.kind == KIND.IN
        assert movement.stock_after == Decimal("20")

    def test_sans_nom_refuse(self, clinic):
        with pytest.raises(ValidationError):
            services.create_product(clinic=clinic, name="  ", category=CAT.MEDICINE)

    def test_prix_negatif_refuse(self, clinic):
        with pytest.raises(ValidationError):
            make_product(clinic, sale_price=Decimal("-1"))
        with pytest.raises(ValidationError):
            make_product(clinic, purchase_price=Decimal("-2"))

    def test_produit_archivable_et_restaurable(self, clinic, product):
        product.archive("fin de gamme")
        product.refresh_from_db()
        assert product.is_active is False
        product.restore()
        assert product.is_active is True

    def test_valeur_du_stock_calculee(self, clinic, product):
        assert product.stock_value == Decimal("200.00")


# ---------------------------------------------------------------------------
# Mouvements et verrou de stock
# ---------------------------------------------------------------------------


class TestMovements:
    def test_entree_replie_le_stock(self, clinic, product):
        services.record_movement(
            clinic, product, kind=KIND.IN, quantity=Decimal("5"), reason="bon CDE-1"
        )
        product.refresh_from_db()
        assert product.stock_quantity == Decimal("25")

    def test_sortie_debit_le_stock_et_conserve_avant_apres(self, clinic, product):
        movement = services.record_movement(
            clinic, product, kind=KIND.OUT, quantity=Decimal("8")
        )
        product.refresh_from_db()
        assert product.stock_quantity == Decimal("12")
        assert movement.stock_before == Decimal("20")
        assert movement.stock_after == Decimal("12")

    def test_sortie_superieure_au_stock_refusee(self, clinic, product):
        with pytest.raises(ValidationError):
            services.record_movement(
                clinic, product, kind=KIND.OUT, quantity=Decimal("999")
            )
        product.refresh_from_db()
        assert product.stock_quantity == Decimal("20")

    def test_quantite_negatif_refusee(self, clinic, product):
        with pytest.raises(ValidationError):
            services.record_movement(
                clinic, product, kind=KIND.IN, quantity=Decimal("-3")
            )

    def test_ajustement_fixe_le_stock(self, clinic, product):
        services.record_movement(
            clinic,
            product,
            kind=KIND.ADJUST,
            quantity=Decimal("13"),
            reason="inventaire",
        )
        product.refresh_from_db()
        assert product.stock_quantity == Decimal("13")

    def test_produit_d_autre_clinique_refuse(self, clinic, other_product):
        with pytest.raises(ValidationError):
            services.record_movement(
                clinic, other_product, kind=KIND.IN, quantity=Decimal("1")
            )

    def test_alerte_stock_bas(self, clinic, product):
        services.record_movement(clinic, product, kind=KIND.OUT, quantity=Decimal("18"))
        product.refresh_from_db()
        assert product.is_low_stock is True
        names = [item.name for item in services.low_stock_products(clinic)]
        assert product.name in names


# ---------------------------------------------------------------------------
# Web
# ---------------------------------------------------------------------------


class TestWeb:
    def test_la_lecture_est_ouverte_a_toute_lequipe(
        self, client, clinic, doctor, product
    ):
        client.force_login(doctor)
        assert client.get(reverse("web:product-list")).status_code == 200
        response = client.get(reverse("web:product-detail", args=[product.pk]))
        assert response.status_code == 200

    def test_le_medecin_ne_peut_pas_creer_un_produit(self, client, clinic, doctor):
        client.force_login(doctor)
        response = client.post(
            reverse("web:product-create"),
            {
                "name": "Seringue 5 ml",
                "category": CAT.CONSUMABLE,
                "unit": "unité",
                "purchase_price": "0.50",
                "sale_price": "1.00",
                "low_stock_threshold": "10",
                "initial_quantity": "100",
                "notes": "",
            },
        )
        assert response.status_code == 403
        assert Product.objects.count() == 0

    def test_creation_via_le_formulaire_web(self, client, clinic, receptionist):
        client.force_login(receptionist)
        response = client.post(
            reverse("web:product-create"),
            {
                "name": "Gel hydroalcoolique",
                "category": CAT.CONSUMABLE,
                "unit": "flacon",
                "barcode": "GL-500",
                "purchase_price": "8.50",
                "sale_price": "12.00",
                "low_stock_threshold": "10",
                "initial_quantity": "30",
                "notes": "",
            },
        )
        assert response.status_code == 302
        product = Product.objects.get()
        assert product.stock_quantity == Decimal("30")
        assert product.movements.count() == 1

    def test_mouvement_web_refuse_si_sortie_excessive(
        self, client, clinic, accountant, product
    ):
        client.force_login(accountant)
        response = client.post(
            reverse("web:product-movement", args=[product.pk]),
            {
                "kind": KIND.OUT,
                "quantity": "1000",
                "reason": "",
                "moved_at": "2026-10-19T10:00",
            },
        )
        assert response.status_code == 200
        product.refresh_from_db()
        assert product.stock_quantity == Decimal("20")

    def test_mouvement_web_ajustement(self, client, clinic, accountant, product):
        client.force_login(accountant)
        response = client.post(
            reverse("web:product-movement", args=[product.pk]),
            {
                "kind": KIND.ADJUST,
                "quantity": "14",
                "reason": "inventaire",
                "moved_at": "2026-10-19T10:00",
            },
        )
        assert response.status_code == 302
        product.refresh_from_db()
        assert product.stock_quantity == Decimal("14")

    def test_le_journal_des_mouvements_est_filtre(
        self, client, clinic, accountant, product
    ):
        services.record_movement(clinic, product, kind=KIND.OUT, quantity=Decimal("2"))
        client.force_login(accountant)
        response = client.get(reverse("web:movement-list"), {"type": "sorties"})
        content = response.content.decode()
        assert response.status_code == 200
        assert product.name in content


# ---------------------------------------------------------------------------
# API mobile
# ---------------------------------------------------------------------------


class TestApi:
    def test_medecin_en_lecture_sans_ecriture(self, clinic, doctor, product):
        api = api_client(doctor)
        assert api.get("/api/v1/products/").status_code == 200
        response = api.post(
            "/api/v1/stock-movements/",
            {"product": product.pk, "kind": KIND.IN, "quantity": "1"},
            format="json",
        )
        assert response.status_code == 403

    def test_liste_et_fiche_produit(self, clinic, accountant, product):
        api = api_client(accountant)
        response = api.get("/api/v1/products/")
        assert response.status_code == 200
        assert response.json()["results"][0]["reference"] == product.reference

        detail = api.get(f"/api/v1/products/{product.pk}/")
        assert detail.status_code == 200
        assert detail.json()["stock_quantity"] == "20.00"

    def test_creation_via_api_avec_stock_initial(self, clinic, accountant):
        api = api_client(accountant)
        response = api.post(
            "/api/v1/products/",
            {
                "name": "Comprimés iodés",
                "category": "MEDICINE",
                "initial_quantity": "12",
                "low_stock_threshold": "4",
            },
            format="json",
        )
        assert response.status_code == 201, response.content
        product = Product.objects.get()
        assert product.stock_quantity == Decimal("12")
        movements = api.get(f"/api/v1/products/{product.pk}/movements/")
        assert movements.status_code == 200
        assert len(movements.json()) == 1

    def test_filtre_stock_bas_via_api(self, clinic, accountant, product):
        services.record_movement(clinic, product, kind=KIND.OUT, quantity=Decimal("18"))
        response = api_client(accountant).get("/api/v1/products/?low_stock=1")
        assert response.status_code == 200
        assert response.json()["results"]

    def test_enregistrement_d_un_mouvement_via_api(self, clinic, accountant, product):
        response = api_client(accountant).post(
            "/api/v1/stock-movements/",
            {"product": product.pk, "kind": KIND.OUT, "quantity": "5", "reason": "soin"},
            format="json",
        )
        assert response.status_code == 201, response.content
        product.refresh_from_db()
        assert product.stock_quantity == Decimal("15")

    def test_mouvement_d_un_produit_d_autre_clinique_refuse(
        self, clinic, accountant, other_product
    ):
        response = api_client(accountant).post(
            "/api/v1/stock-movements/",
            {"product": other_product.pk, "kind": KIND.IN, "quantity": "1"},
            format="json",
        )
        assert response.status_code == 400
        other_product.refresh_from_db()
        assert other_product.stock_quantity == Decimal("20")

    def test_suppression_refusee(self, clinic, accountant, product):
        response = api_client(accountant).delete(f"/api/v1/products/{product.pk}/")
        assert response.status_code in (403, 400)
        assert Product.objects.filter(pk=product.pk).exists()

    def test_cloisonnement_entre_cliniques(self, clinic, accountant, other_product):
        response = api_client(accountant).get("/api/v1/products/")
        assert response.status_code == 200
        assert response.json()["results"] == []
