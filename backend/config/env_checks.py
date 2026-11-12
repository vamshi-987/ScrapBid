"""Start-up checks for environment settings."""

DEFAULT_SECRET_KEY = "insecure-local-dev-key-change-me"


def production_problems(
    *,
    debug,
    secret_key,
    otp_provider,
    dev_fixed_otp,
    payment_provider,
    razorpay_key_id,
    razorpay_key_secret,
    app_base_url,
):
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
    if payment_provider != "razorpay":
        problems.append("PAYMENT_PROVIDER must be 'razorpay'")
    elif not razorpay_key_id or not razorpay_key_secret:
        problems.append("RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET must be set")
    if not app_base_url.startswith("https://"):
        problems.append("APP_BASE_URL must be an https:// address")
    return problems
