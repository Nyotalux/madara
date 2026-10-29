"""Routes web du stock (namespace ``web``)."""

from __future__ import annotations

from django.urls import path

from . import views

urlpatterns = [
    path("stock/", views.product_list, name="product-list"),
    path("stock/nouveau/", views.product_create, name="product-create"),
    path("stock/mouvements/", views.movement_list, name="movement-list"),
    path("stock/<int:pk>/", views.product_detail, name="product-detail"),
    path("stock/<int:pk>/modifier/", views.product_edit, name="product-edit"),
    path("stock/<int:pk>/archiver/", views.product_archive, name="product-archive"),
    path("stock/<int:pk>/restaurer/", views.product_restore, name="product-restore"),
    path("stock/<int:pk>/mouvement/", views.product_movement, name="product-movement"),
]
