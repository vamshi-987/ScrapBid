import os
from pathlib import Path

from botocore.config import Config as BotoConfig
from django.core.exceptions import ImproperlyConfigured

from config.env_checks import DEFAULT_SECRET_KEY, production_problems

BASE_DIR = Path(__file__).resolve().parent.parent


def env(name, default=""):
    return os.environ.get(name, default)


def env_bool(name, default=False):
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def env_list(name, default=""):
    return [item.strip() for item in env(name, default).split(",") if item.strip()]


# --- Environment -----------------------------------------------------------

DJANGO_ENV = env("DJANGO_ENV", "local")
if DJANGO_ENV not in {"local", "production"}:
    raise ImproperlyConfigured("DJANGO_ENV must be 'local' or 'production'")

DEBUG = env_bool("DJANGO_DEBUG")
SECRET_KEY = env("DJANGO_SECRET_KEY") or DEFAULT_SECRET_KEY
ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1")

OTP_PROVIDER = env("OTP_PROVIDER", "console")
DEV_FIXED_OTP = env("DEV_FIXED_OTP")

if DJANGO_ENV == "production":
    _problems = production_problems(
        debug=DEBUG,
        secret_key=SECRET_KEY,
        otp_provider=OTP_PROVIDER,
        dev_fixed_otp=DEV_FIXED_OTP,
    )
    if _problems:
        raise ImproperlyConfigured("Refusing to start in production: " + "; ".join(_problems))

# --- Applications ----------------------------------------------------------

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.gis",
    "rest_framework",
    "accounts",
    "dealers",
    "listings",
    "offers",
    "orders",
    "notifications",
    "audit",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"

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

AUTH_USER_MODEL = "accounts.User"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --- Database --------------------------------------------------------------

DATABASES = {
    "default": {
        "ENGINE": "django.contrib.gis.db.backends.postgis",
        "NAME": env("POSTGRES_DB", "scrapbid"),
        "USER": env("POSTGRES_USER", "scrapbid"),
        "PASSWORD": env("POSTGRES_PASSWORD"),
        "HOST": env("POSTGRES_HOST", "db"),
        "PORT": env("POSTGRES_PORT", "5432"),
        "OPTIONS": {"connect_timeout": 3},
    }
}

# --- File storage (S3 API: MinIO locally, AWS S3 in production) -----------

S3_ENDPOINT_URL = env("S3_ENDPOINT_URL") or None

STORAGES = {
    "default": {
        "BACKEND": "storages.backends.s3.S3Storage",
        "OPTIONS": {
            "bucket_name": env("S3_BUCKET"),
            "endpoint_url": S3_ENDPOINT_URL,
            "access_key": env("S3_ACCESS_KEY_ID"),
            "secret_key": env("S3_SECRET_ACCESS_KEY"),
            "region_name": env("S3_REGION", "us-east-1"),
            "addressing_style": "path" if S3_ENDPOINT_URL else "virtual",
            "signature_version": "s3v4",
            "default_acl": None,
            "querystring_auth": True,
            "file_overwrite": False,
            "client_config": BotoConfig(
                connect_timeout=3, read_timeout=10, retries={"max_attempts": 2}
            ),
        },
    },
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

# --- API -------------------------------------------------------------------

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": ["rest_framework.authentication.SessionAuthentication"],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "DEFAULT_RENDERER_CLASSES": ["rest_framework.renderers.JSONRenderer"],
}

# --- Localisation: stored in UTC, shown in IST -----------------------------

LANGUAGE_CODE = "en-in"
TIME_ZONE = "Asia/Kolkata"
USE_I18N = True
USE_TZ = True

# --- Logging ---------------------------------------------------------------

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": "INFO"},
}
