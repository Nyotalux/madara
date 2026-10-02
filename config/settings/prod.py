"""Réglages de production.

Exigences de securite :
  - SECRET_KEY, POSTGRES_PASSWORD et REDIS_URL fournis par l'environnement
  - DEBUG=False
  - ALLOWED_HOSTS et CORS_ALLOWED_ORIGINS explicitement definis
"""

from .base import *  # noqa: F403
from .base import env

DEBUG = False

ALLOWED_HOSTS = env.list("ALLOWED_HOSTS")

SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = env.bool("SECURE_SSL_REDIRECT", default=True)
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_HSTS_SECONDS = 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"

EMAIL_BACKEND = env(
    "EMAIL_BACKEND", default="django.core.mail.backends.smtp.EmailBackend"
)

# Sessions et messages Stockés en base (plusieurs instances).
SESSION_ENGINE = "django.contrib.sessions.backends.db"

# Le journal d'audit des dossiers medicaux ne doit pas disparaitre au rotation.
FILE_UPLOAD_PERMISSIONS = 0o640

CONN_HEALTH_CHECKS = True

LOG_LEVEL = env("LOG_LEVEL", default="INFO")

# Verification de la configuration sensible au demarrage (fail fast).
_required = ["SECRET_KEY", "POSTGRES_PASSWORD", "REDIS_URL"]
_missing = [key for key in _required if not env(key, default="")]
if _missing:
    raise RuntimeError(
        "Variables d'environnement manquantes pour la production : "
        + ", ".join(_missing)
    )