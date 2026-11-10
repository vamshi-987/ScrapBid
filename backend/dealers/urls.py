from django.urls import path

from dealers import views

urlpatterns = [
    path("profile", views.DealerProfileView.as_view(), name="dealer-profile"),
    path("documents", views.DealerDocumentsView.as_view(), name="dealer-documents"),
]
