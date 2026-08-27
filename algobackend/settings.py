"""
Django settings for algobackend (trading backend).

Datastore note: business data lives in Supabase (Postgres) accessed via the
supabase client. Django's own DB below is only for Django internals
(admin/sessions/contenttypes/migrations). Default sqlite is fine for that;
point DATABASE_URL at Postgres in production if you prefer.
"""

import os
from pathlib import Path

import dj_database_url
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

# Load .env from repo root
load_dotenv(BASE_DIR / ".env")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _env_bool(name: str, default: str = "False") -> bool:
    return os.getenv(name, default).strip().lower() in ("1", "true", "yes", "on")


def _env_list(name: str, default: str = "") -> list[str]:
    raw = os.getenv(name, default)
    return [item.strip() for item in raw.split(",") if item.strip()]


def _format_pem_key(key: str) -> str:
    """Accept single-line or multi-line PEM from .env and normalise to PEM."""
    if not key:
        return ""
    key = key.strip()
    if "-----BEGIN" in key and "\n" in key:
        return key  # already multi-line PEM
    if "PUBLIC KEY" in key:
        header, footer = "-----BEGIN PUBLIC KEY-----", "-----END PUBLIC KEY-----"
    elif "PRIVATE KEY" in key:
        header, footer = "-----BEGIN PRIVATE KEY-----", "-----END PRIVATE KEY-----"
    else:
        return key
    content = key.replace(header, "").replace(footer, "").strip()
    lines = [content[i:i + 64] for i in range(0, len(content), 64)]
    return f"{header}\n" + "\n".join(lines) + f"\n{footer}"


# ---------------------------------------------------------------------------
# Secrets / external services (all from env)
# ---------------------------------------------------------------------------
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
SUPABASE_JWT_SECRET = os.getenv("SUPABASE_JWT_SECRET")

PUBLIC_KEY = _format_pem_key(os.getenv("ANGEL_PUBLIC", ""))   # RSA-OAEP: api_key/pin/totp
PRIVATE_KEY = _format_pem_key(os.getenv("ANGEL_PRIVATE", ""))
FERNET = os.getenv("FERNET")                                  # Fernet: jwt/feed/refresh tokens


# ---------------------------------------------------------------------------
# Core Django
# ---------------------------------------------------------------------------
# CHANGED vs old project: was a hardcoded "django-insecure-..." value. Now env-driven.
SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "dev-insecure-change-me")

# CHANGED vs old project: was DEBUG = True hardcoded. Now env-driven, defaults False.
DEBUG = _env_bool("DJANGO_DEBUG", "False")

# CHANGED vs old project: was a hardcoded list. Now env-driven (comma-separated).
ALLOWED_HOSTS = _env_list("DJANGO_ALLOWED_HOSTS", "127.0.0.1,localhost,192.168.1.5",)


INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # third-party
    "rest_framework",
    "corsheaders",
    # local apps
    "apps.accounts",
    "apps.strategies",
    "apps.execution",
    "apps.marketdata",
    "apps.scheduling",
    "apps.admin_api",
]

MIDDLEWARE = [
    "corsheaders.middleware.CorsMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "algobackend.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "algobackend.wsgi.application"
ASGI_APPLICATION = "algobackend.asgi.application"


# ---------------------------------------------------------------------------
# Database (Django internals only; business data is in Supabase)
# Defaults to sqlite; set DATABASE_URL=postgres://... to override.
# ---------------------------------------------------------------------------
DATABASES = {
    "default": dj_database_url.config(
        default=f"sqlite:///{BASE_DIR / 'db.sqlite3'}",
        conn_max_age=600,
    )
}


AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]


# ---------------------------------------------------------------------------
# i18n / tz
# Keep Django in UTC + USE_TZ; IST is handled explicitly in trading logic and
# in CELERY_TIMEZONE below.
# ---------------------------------------------------------------------------
LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"


# ---------------------------------------------------------------------------
# DRF (minimal; Supabase-JWT auth wired in the accounts app later)
# ---------------------------------------------------------------------------
REST_FRAMEWORK = {
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
    "DEFAULT_PARSER_CLASSES": ["rest_framework.parsers.JSONParser"],
    # TODO(accounts phase): replace with a Supabase-JWT auth/permission class.
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.AllowAny"],
}


# ---------------------------------------------------------------------------
# CORS
# ---------------------------------------------------------------------------
# CHANGED vs old project: was CORS_ALLOW_ALL_ORIGINS = True unconditionally.
# Allow-all only in DEBUG; in prod use an explicit allow-list via env.
CORS_ALLOW_ALL_ORIGINS = DEBUG
CORS_ALLOWED_ORIGINS = _env_list("CORS_ALLOWED_ORIGINS", "")


# ---------------------------------------------------------------------------
# Celery
# ---------------------------------------------------------------------------
CELERY_BROKER_URL = os.getenv("CELERY_BROKER_URL", "redis://localhost:6379/0")
CELERY_RESULT_BACKEND = os.getenv("CELERY_RESULT_BACKEND", "redis://localhost:6379/0")
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_TIMEZONE = "Asia/Kolkata"   # IST — India has no DST, so this is fixed +5:30
CELERY_ENABLE_UTC = False

os.environ.setdefault("FORKED_BY_MULTIPROCESSING", "1")


# ---------------------------------------------------------------------------
# Logging (base console config; the consolidated logger factory lands in
# core/logging during Phase 1 and can extend this).
# ---------------------------------------------------------------------------
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {
            "format": "{asctime} [{levelname}] {name}: {message}",
            "style": "{",
            "datefmt": "%Y-%m-%d %H:%M:%S",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "verbose",
        },
        "file": {
            "class": "logging.handlers.RotatingFileHandler",
            "filename": BASE_DIR / "logs" / "algo.log",
            "maxBytes": 10 * 1024 * 1024,   # 10 MB per file
            "backupCount": 7,                # keep 7 days
            "formatter": "verbose",
        },
    },
    "root": {
        "handlers": ["console", "file"],
        "level": "INFO",
    },
    "loggers": {
        # Strategy logs at DEBUG so P&L ticks are visible in file
        "strategy": {
            "handlers": ["console", "file"],
            "level": "DEBUG",
            "propagate": False,
        },
    },
}