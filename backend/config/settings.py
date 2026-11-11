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


def env_positive_int(name, default):
    raw = env(name) or str(default)
    try:
        value = int(raw)
    except ValueError:
        raise ImproperlyConfigured(f"{name} must be a whole number") from None
    if value <= 0:
        raise ImproperlyConfigured(f"{name} must be greater than 0")
    return value


# --- Environment -----------------------------------------------------------

DJANGO_ENV = env("DJANGO_ENV", "local")
if DJANGO_ENV not in {"local", "production"}:
    raise ImproperlyConfigured("DJANGO_ENV must be 'local' or 'production'")

DEBUG = env_bool("DJANGO_DEBUG")
SECRET_KEY = env("DJANGO_SECRET_KEY") or DEFAULT_SECRET_KEY
ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1")

OTP_PROVIDER = env("OTP_PROVIDER", "console")
DEV_FIXED_OTP = env("DEV_FIXED_OTP")
OTP_EXPIRY_SECONDS = env_positive_int("OTP_EXPIRY_SECONDS", 300)
OTP_MAX_ATTEMPTS = env_positive_int("OTP_MAX_ATTEMPTS", 5)
OTP_MAX_REQUESTS_PER_PHONE_10_MIN = env_positive_int("OTP_MAX_REQUESTS_PER_PHONE_10_MIN", 3)
OTP_MAX_REQUESTS_PER_PHONE_DAY = env_positive_int("OTP_MAX_REQUESTS_PER_PHONE_DAY", 10)
OTP_MAX_REQUESTS_PER_IP_HOUR = env_positive_int("OTP_MAX_REQUESTS_PER_IP_HOUR", 20)

DEALER_DOCUMENT_MAX_BYTES = env_positive_int("DEALER_DOCUMENT_MAX_BYTES", 10 * 1024 * 1024)

LISTING_PHOTO_MAX_BYTES = env_positive_int("LISTING_PHOTO_MAX_BYTES", 10 * 1024 * 1024)
LISTING_MAX_PHOTOS = env_positive_int("LISTING_MAX_PHOTOS", 6)
# Largest declared weight for one category on a listing, in kg (bulk dealer loads fit).
LISTING_MAX_WEIGHT_KG = env_positive_int("LISTING_MAX_WEIGHT_KG", 10_000)

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
# Address the browser uses for presigned links (Caddy locally). Falls back to the
# storage endpoint; both empty (production) means AWS S3.
S3_PUBLIC_ENDPOINT_URL = env("S3_PUBLIC_ENDPOINT_URL") or S3_ENDPOINT_URL
PRESIGNED_URL_TTL_SECONDS = env_positive_int("PRESIGNED_URL_TTL_SECONDS", 600)

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
