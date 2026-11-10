"""Presigned links to private files, for the browser.

Signed for S3_PUBLIC_ENDPOINT_URL, the address the browser uses: locally Caddy
on http://localhost forwards the bucket path to MinIO; in production the setting
is empty and links point at AWS S3 directly.
"""

from functools import lru_cache

import boto3
from django.conf import settings
from django.core.files.storage import default_storage


@lru_cache(maxsize=1)
def _signing_client(endpoint_url):
    options = settings.STORAGES["default"]["OPTIONS"]
    return boto3.client(
        "s3",
        endpoint_url=endpoint_url,
        aws_access_key_id=options["access_key"],
        aws_secret_access_key=options["secret_key"],
        region_name=options["region_name"],
        config=boto3.session.Config(
            signature_version="s3v4", s3={"addressing_style": options["addressing_style"]}
        ),
    )


def presigned_url(name, expires_in=None):
    """A time-limited GET link for a stored file. Never a permanent public URL."""
    key = "/".join(part for part in (default_storage.location, name) if part)
    client = _signing_client(settings.S3_PUBLIC_ENDPOINT_URL)
    return client.generate_presigned_url(
        "get_object",
        Params={"Bucket": default_storage.bucket_name, "Key": key},
        ExpiresIn=expires_in or settings.PRESIGNED_URL_TTL_SECONDS,
    )
