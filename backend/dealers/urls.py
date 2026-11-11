from django.urls import path

from dealers import views
from listings.views import DealerListingDetailView, DealerListingFeedView

urlpatterns = [
    path("profile", views.DealerProfileView.as_view(), name="dealer-profile"),
    path("documents", views.DealerDocumentsView.as_view(), name="dealer-documents"),
    path("listings", DealerListingFeedView.as_view(), name="dealer-listing-feed"),
    path(
        "listings/<int:listing_id>",
        DealerListingDetailView.as_view(),
        name="dealer-listing-detail",
    ),
]
