"""Réglages de développement local."""

from .base import *  # noqa: F403
from .base import TEMPLATES, env

DEBUG = env.bool("DEBUG", default=True)

ALLOWED_HOSTS = env.list(
    "ALLOWED_HOSTS", default=["localhost", "127.0.0.1", "0.0.0.0", "[::1]"]
)

EMAIL_BACKEND = env(
    "EMAIL_BACKEND",
    default="django.core.mail.backends.console.EmailBackend",
)

STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}

# Les erreurs de gabarit et les requetes lentes doivent sauter aux yeux.
TEMPLATES[0]["OPTIONS"]["debug"] = True

INTERNAL_IPS = ["127.0.0.1"]

# Evite d'envoyer de vrais e-mails en developpement.
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", default="no-reply@madara.local")

# Taille des lots des commandes de gestion (seed, imports).
MADARA_BATCH_SIZE = 500
