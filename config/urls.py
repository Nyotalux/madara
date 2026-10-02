"""Routage HTTP du projet."""

from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView

from apps.common.views import HealthView, health_db, health_ready

admin.site.site_header = "Madara - Administration"
admin.site.site_title = "Madara"
admin.site.index_title = "Administration de la plateforme"

urlpatterns = [
    # --- Sante (monitoring)
    path("health/", HealthView.as_view(), name="health"),
    path("health/db/", health_db, name="health-db"),
    path("health/ready/", health_ready, name="health-ready"),
    # --- Back-office Django
    path("admin/", admin.site.urls),
    # --- API REST (applications mobiles)
    path("api/v1/auth/", include("apps.accounts.urls_auth")),
    path("api/v1/", include("apps.accounts.urls_api")),
    path("api/v1/schema/", SpectacularAPIView.as_view(), name="api-schema"),
    path(
        "api/v1/docs/",
        SpectacularSwaggerView.as_view(url_name="api-schema"),
        name="api-docs",
    ),
    # --- Interface web du personnel (namespace « web »)
    path("", include("config.urls_web")),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
