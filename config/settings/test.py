"""Réglages pour la suite de tests (pytest)."""

from .base import *  # noqa: F403
from .base import env

DEBUG = False

ALLOWED_HOSTS = ["testserver", "localhost"]

# Fausse base : pytest-django cree puis detruit la base de test.
DATABASES["default"]["TEST"] = {"NAME": env(  # noqa: F405
    "POSTGRES_TEST_DB", default="test_madara"
)}

# Aucun envoi reel : tout est capture en memoire.
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
NOTIFICATIONS = {  # noqa: F405
    **NOTIFICATIONS,  # noqa: F405
    "WHATSAPP_PROVIDER": "console",
}

CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True

# Les hachages de mots de passe les plus lents ecrasent la vitesse.
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

STORAGES = {  # noqa: F405
    "default": {"BACKEND": "django.core.files.storage.InMemoryStorage"},
    "staticfiles": {
        "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"
    },
}

MEDIA_ROOT = None

# Unittest : pas de migrations concurrentes, on garde le temps de la logique.
CELERY_BROKER_URL = "memory://"
CELERY_RESULT_BACKEND = "cache+memory://"

USE_TZ = True
TIME_ZONE = "UTC"
LANGUAGE_CODE = "fr"