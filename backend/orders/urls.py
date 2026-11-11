from django.urls import path

from orders import views

urlpatterns = [
    path("offers/<int:offer_id>/accept", views.AcceptOfferView.as_view(), name="offer-accept"),
]
