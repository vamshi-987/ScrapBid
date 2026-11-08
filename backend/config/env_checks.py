"""Start-up checks for environment settings."""

DEFAULT_SECRET_KEY = "insecure-local-dev-key-change-me"


def production_problems(*, debug, secret_key, otp_provider, dev_fixed_otp):
    """Return the reasons production settings are unsafe. An empty list means they are safe."""
    problems = []
    if debug:
        problems.append("DJANGO_DEBUG must be False")
    if not secret_key or secret_key == DEFAULT_SECRET_KEY:
        problems.append("DJANGO_SECRET_KEY must be set to a non-default value")
    if otp_provider == "console":
        problems.append("OTP_PROVIDER must not be 'console'")
    if dev_fixed_otp:
        problems.append("DEV_FIXED_OTP must not be set")
    return problems
