from django.urls import path

from dealers import views
from listings.views import DealerListingDetailView, DealerListingFeedView
from offers import views as offer_views

urlpatterns = [
    path("profile", views.DealerProfileView.as_view(), name="dealer-profile"),
    path("documents", views.DealerDocumentsView.as_view(), name="dealer-documents"),
    path("listings", DealerListingFeedView.as_view(), name="dealer-listing-feed"),
    path(
        "listings/<int:listing_id>",
        DealerListingDetailView.as_view(),
        name="dealer-listing-detail",
    ),
    path(
        "listings/<int:listing_id>/offers",
        offer_views.ListingOffersView.as_view(),
        name="dealer-listing-offers",
    ),
    path("offers", offer_views.MyOffersView.as_view(), name="dealer-offers"),
    path("offers/<int:offer_id>", offer_views.OfferDetailView.as_view(), name="dealer-offer"),
    path(
        "offers/<int:offer_id>/withdraw",
        offer_views.OfferWithdrawView.as_view(),
        name="dealer-offer-withdraw",
    ),
]
