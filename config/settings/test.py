"""Réglages pour la suite de tests (pytest)."""

from .base import *  # noqa: F403
from .base import DATABASES, NOTIFICATIONS, env

DEBUG = False

ALLOWED_HOSTS = ["testserver", "localhost"]

# pytest-django cree puis detruit cette base pour chaque execution.
DATABASES["default"]["TEST"] = {"NAME": env("POSTGRES_TEST_DB", default="test_madara")}

# Aucun envoi reel : les e-mails sont captures en memoire.
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
NOTIFICATIONS = {**NOTIFICATIONS, "WHATSAPP_PROVIDER": "console"}

# Les hachages les plus lents ecrasent la vitesse.
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.InMemoryStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}

# Les taches s'executent de maniere synchrone : les assertions portent sur l'effet.
CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True
CELERY_BROKER_URL = "memory://"
CELERY_RESULT_BACKEND = "cache+memory://"

LANGUAGE_CODE = "fr"
TIME_ZONE = "UTC"
USE_TZ = True
