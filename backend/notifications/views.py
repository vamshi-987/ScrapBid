from django.shortcuts import get_object_or_404
from rest_framework import serializers, status
from rest_framework.generics import ListAPIView
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response
from rest_framework.views import APIView

from notifications.models import Notification
from notifications.services import mark_read


class NotificationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Notification
        fields = ["id", "kind", "data", "read_at", "created_at"]


class NotificationPagination(PageNumberPagination):
    page_size = 20


def own(request):
    return Notification.objects.filter(user=request.user)


class NotificationListView(ListAPIView):
    serializer_class = NotificationSerializer
    pagination_class = NotificationPagination

    def get_queryset(self):
        return own(self.request)


class UnreadCountView(APIView):
    def get(self, request):
        return Response({"unread": own(request).filter(read_at__isnull=True).count()})


class MarkReadView(APIView):
    def post(self, request, notification_id):
        notification = get_object_or_404(own(request), pk=notification_id)
        mark_read(own(request).filter(pk=notification.pk))
        notification.refresh_from_db()
        return Response(NotificationSerializer(notification).data)


class MarkAllReadView(APIView):
    def post(self, request):
        mark_read(own(request))
        return Response(status=status.HTTP_204_NO_CONTENT)
