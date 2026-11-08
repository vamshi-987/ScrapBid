import logging

from django.core.files.storage import default_storage
from django.db import connection
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

logger = logging.getLogger(__name__)


def check_database():
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1")


def check_storage():
    default_storage.bucket.meta.client.head_bucket(Bucket=default_storage.bucket_name)


class HealthView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    def get(self, request):
        checks = {"database": check_database, "storage": check_storage}
        results = {}
        for name, check in checks.items():
            try:
                check()
                results[name] = "ok"
            except Exception:
                logger.exception("Health check failed: %s", name)
                results[name] = "error"
        healthy = all(result == "ok" for result in results.values())
        return Response(
            {"status": "ok" if healthy else "error", "checks": results},
            status=200 if healthy else 503,
        )
