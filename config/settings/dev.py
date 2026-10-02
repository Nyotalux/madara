"""Réglages de développement local."""

from .base import *  # noqa: F403
from .base import BASE_DIR, env  # noqa: F401

DEBUG = env.bool("DEBUG", default=True)

ALLOWED_HOSTS = env.list(
    "ALLOWED_HOSTS", default=["localhost", "127.0.0.1", "0.0.0.0", "[::1]"]
)

EMAIL_BACKEND = env(
    "EMAIL_BACKEND",
    default="django.core.mail.backends.console.EmailBackend",
)

CELERY_TASK_ALWAYS_EAGER = env.bool("CELERY_TASK_ALWAYS_EAGER", default=False)
CELERY_TASK_EAGER_PROPAGATES = True

# Les erreurs de gabarit et les requetes SQL lentes remontent immediatement.
TEMPLATES[0]["OPTIONS"]["debug"] = True  # noqa: F405
TEMPLATES[0]["APP_DIRS"] = True  # noqa: F405

INTERNAL_IPS = ["127.0.0.1"]

STORAGES = {  # noqa: F405
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"
    },
}

# Evite d'envoyer de vrais e-mails en developpement.
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", default="no-reply@madara.local")

# Taille des lots pour les commandes de gestion (seed, imports).
MADARA_BATCH_SIZE = 500