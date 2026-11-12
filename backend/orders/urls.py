from django.urls import path

from orders import views

urlpatterns = [
    path("offers/<int:offer_id>/accept", views.AcceptOfferView.as_view(), name="offer-accept"),
    path("orders", views.OrderListView.as_view(), name="order-list"),
    path("orders/<int:order_id>", views.OrderDetailView.as_view(), name="order-detail"),
    path("orders/<int:order_id>/contact", views.OrderContactView.as_view(), name="order-contact"),
    path("orders/<int:order_id>/slot", views.ProposeSlotView.as_view(), name="order-slot"),
    path(
        "orders/<int:order_id>/slot/confirm",
        views.ConfirmSlotView.as_view(),
        name="order-slot-confirm",
    ),
    path(
        "orders/<int:order_id>/reschedule",
        views.RequestRescheduleView.as_view(),
        name="order-reschedule",
    ),
    path(
        "orders/<int:order_id>/reschedule/accept",
        views.AnswerRescheduleView.as_view(accept=True),
        name="order-reschedule-accept",
    ),
    path(
        "orders/<int:order_id>/reschedule/decline",
        views.AnswerRescheduleView.as_view(accept=False),
        name="order-reschedule-decline",
    ),
]
