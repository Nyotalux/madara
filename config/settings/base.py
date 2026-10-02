"""Réglages communs à tous les environnements (base, dev, prod, test)."""

from datetime import timedelta
from pathlib import Path

import environ

BASE_DIR = Path(__file__).resolve().parent.parent.parent

env = environ.Env(
    DEBUG=(bool, False),
    ALLOWED_HOSTS=(list, []),
    # SECRET_KEY volontairement vide : jamais de valeur par défaut en dur.
    SECRET_KEY=(str, ""),
)

# Le fichier .env est facultatif (utile en CI / tests).
_env_file = BASE_DIR / ".env"
if _env_file.exists():
    environ.Env.read_env(_env_file)


# ---------------------------------------------------------------------------
# Coeur
# ---------------------------------------------------------------------------

SECRET_KEY = env("SECRET_KEY")

DEBUG = env("DEBUG")

ALLOWED_HOSTS = env("ALLOWED_HOSTS")

AUTH_USER_MODEL = "accounts.User"

ROOT_URLCONF = "config.urls"

WSGI_APPLICATION = "config.wsgi.application"

ASGI_APPLICATION = "config.asgi.application"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

INSTALLED_APPS = [
    # --- Django
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # --- Librairies
    "django_htmx",
    "rest_framework",
    "django_filters",
    # --- Applications du projet
    "apps.common",
    "apps.accounts",
    "apps.patients",
    "apps.appointments",
    "apps.consultations",
    "apps.medical_records",
    "apps.notifications",
    "apps.billing",
    "apps.inventory",
    "apps.payroll",
    "apps.dashboard",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.locale.LocaleMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "django_htmx.middleware.HtmxMiddleware",
    # Résout la clinique courante (session web / en-tête X-Clinic en API).
    "apps.common.middleware.CurrentClinicMiddleware",
]

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "apps.common.context_processors.site_context",
            ],
        },
    },
]


# ---------------------------------------------------------------------------
# Base de donnees
# ---------------------------------------------------------------------------

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": env("POSTGRES_DB", default="madara"),
        "USER": env("POSTGRES_USER", default="madara"),
        "PASSWORD": env("POSTGRES_PASSWORD", default="madara"),
        "HOST": env("POSTGRES_HOST", default="localhost"),
        "PORT": env("POSTGRES_PORT", default="5432"),
        "CONN_MAX_AGE": env("POSTGRES_CONN_MAX_AGE", default=60, cast=int),
        "TEST": {"NAME": env("POSTGRES_TEST_DB", default="test_madara")},
    }
}


# ---------------------------------------------------------------------------
# Mot de passe / authentification
# ---------------------------------------------------------------------------

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": (
            "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"
        )
    },
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.Argon2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2PasswordHasher",
    "django.contrib.auth.hashers.PBKDF2SHA1PasswordHasher",
    "django.contrib.auth.hashers.ScryptPasswordHasher",
]

LOGIN_URL = "web:login"
LOGIN_REDIRECT_URL = "web:dashboard"
LOGOUT_REDIRECT_URL = "web:login"


# ---------------------------------------------------------------------------
# Internationalisation (français par défaut)
# ---------------------------------------------------------------------------

LANGUAGE_CODE = "fr"
TIME_ZONE = env("TIME_ZONE", default="Africa/Casablanca")
USE_I18N = True
USE_TZ = True
LOCALE_PATHS = [BASE_DIR / "locale"]


# ---------------------------------------------------------------------------
# Fichiers statiques et médias
# ---------------------------------------------------------------------------

STATIC_URL = "static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"

MEDIA_URL = "media/"
MEDIA_ROOT = BASE_DIR / "media"

STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"
    },
}

# Types de fichiers acceptes pour les pieces jointes (scans, ordonnances...).
UPLOAD_ALLOWED_EXTENSIONS = [
    "pdf",
    "jpg",
    "jpeg",
    "png",
    "webp",
    "heic",
    "doc",
    "docx",
    "tif",
    "tiff",
]
MAX_UPLOAD_SIZE_MB = int(env("MAX_UPLOAD_SIZE_MB", default=15))


# ---------------------------------------------------------------------------
# API REST (applications mobiles)
# ---------------------------------------------------------------------------

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": (
        "rest_framework_simplejwt.authentication.JWTAuthentication",
        "rest_framework.authentication.SessionAuthentication",
    ),
    "DEFAULT_PERMISSION_CLASSES": (
        "rest_framework.permissions.IsAuthenticated",
    ),
    "DEFAULT_FILTER_BACKENDS": (
        "django_filters.rest_framework.DjangoFilterBackend",
        "rest_framework.filters.SearchFilter",
        "rest_framework.filters.OrderingFilter",
    ),
    "DEFAULT_PAGINATION_CLASS": "apps.common.api.DefaultPagination",
    "PAGE_SIZE": 25,
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "EXCEPTION_HANDLER": "apps.common.api.madara_exception_handler",
    "DATETIME_FORMAT": "%Y-%m-%dT%H:%M:%S%z",
}

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(hours=1),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=14),
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": False,
    "UPDATE_LAST_LOGIN": True,
    "AUTH_HEADER_TYPES": ("Bearer",),
}

SPECTACULAR_SETTINGS = {
    "TITLE": "Madara - API de gestion de clinique",
    "DESCRIPTION": (
        "API REST du systeme de gestion de clinique medicale : patients, "
        "rendez-vous, consultations, dossier medical, finances, stock et paie."
    ),
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
    "SCHEMA_PATH_PREFIX": "/api/v1",
    "COMPONENT_SPLIT_REQUEST": True,
    "SORT_OPERATIONS": False,
}

CORS_ALLOWED_ORIGINS = env("CORS_ALLOWED_ORIGINS", default=[])
CORS_ALLOW_CREDENTIALS = True


# ---------------------------------------------------------------------------
# Celery / Redis
# ---------------------------------------------------------------------------

REDIS_URL = env("REDIS_URL", default="redis://localhost:6379/0")

CELERY_BROKER_URL = env("CELERY_BROKER_URL", default=REDIS_URL)
CELERY_RESULT_BACKEND = env("CELERY_RESULT_BACKEND", default=REDIS_URL)
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TIMEZONE = TIME_ZONE
CELERY_ENABLE_UTC = True
CELERY_TASK_TRACK_STARTED = True
CELERY_TASK_TIME_LIMIT = 60 * 10
CELERY_WORKER_HIJACK_ROOT_LOGGER = False


# ---------------------------------------------------------------------------
# Notifications
# ---------------------------------------------------------------------------

NOTIFICATIONS = {
    # console | meta | twilio
    "WHATSAPP_PROVIDER": env("WHATSAPP_PROVIDER", default="console"),
    "META_WHATSAPP_TOKEN": env("META_WHATSAPP_TOKEN", default=""),
    "META_WHATSAPP_PHONE_ID": env("META_WHATSAPP_PHONE_ID", default=""),
    "META_WHATSAPP_API_VERSION": env(
        "META_WHATSAPP_API_VERSION", default="v21.0"
    ),
    "TWILIO_ACCOUNT_SID": env("TWILIO_ACCOUNT_SID", default=""),
    "TWILIO_AUTH_TOKEN": env("TWILIO_AUTH_TOKEN", default=""),
    "TWILIO_WHATSAPP_FROM": env("TWILIO_WHATSAPP_FROM", default=""),
    "DEFAULT_FROM_EMAIL": env("DEFAULT_FROM_EMAIL", default="no-reply@madara.local"),
    "DEFAULT_FROM_NAME": env("DEFAULT_FROM_NAME", default="Madara Clinique"),
}

EMAIL_BACKEND = env(
    "EMAIL_BACKEND", default="django.core.mail.backends.console.EmailBackend"
)
EMAIL_HOST = env("EMAIL_HOST", default="localhost")
EMAIL_PORT = env("EMAIL_PORT", default=587, cast=int)
EMAIL_HOST_USER = env("EMAIL_HOST_USER", default="")
EMAIL_HOST_PASSWORD = env("EMAIL_HOST_PASSWORD", default="")
EMAIL_USE_TLS = env("EMAIL_USE_TLS", default=True, cast=bool)
EMAIL_TIMEOUT = 10


# ---------------------------------------------------------------------------
# Domaine metier
# ---------------------------------------------------------------------------

# Delais de rappel avant un rendez-vous : (code, delai, canaux par defaut).
APPOINTMENT_REMINDER_SCHEDULE = [
    ("J1_WHATSAPP", 24, ["whatsapp", "email"]),
    ("H2_IN_APP", 2, ["in_app"]),
    ("H1_PATIENT", 1, ["in_app", "email"]),
]

# Duree par defaut d'une consultation, en minutes (premiere visite / suivi).
CONSULTATION_DURATION_MINUTES = 20
FOLLOW_UP_DURATION_MINUTES = 15

# Taux de TVA par defaut (0 = exonere, modifiable par clinique).
DEFAULT_TAX_RATE = env("DEFAULT_TAX_RATE", default="0.00", cast=float)

# Horizon maximal d'un lien de partage de dossier (jours).
RECORD_SHARE_MAX_DAYS = 30

# Delai de conservation propose avant archivage du dossier (mois).
MEDICAL_RECORD_RETENTION_MONTHS = 120


# ---------------------------------------------------------------------------
# Journalisation
# ---------------------------------------------------------------------------

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {
            "format": "[{asctime}] {levelname} {name} {message}",
            "style": "{",
        }
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "verbose",
        }
    },
    "root": {"handlers": ["console"], "level": env("LOG_LEVEL", default="INFO")},
    "loggers": {
        "django.db.backends": {"level": "WARNING", "handlers": ["console"], "propagate": False},
    },
}