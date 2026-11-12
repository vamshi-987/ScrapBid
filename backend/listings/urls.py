from django.urls import path

from listings import views

urlpatterns = [
    path("categories", views.CategoryListView.as_view(), name="category-list"),
    path("listings", views.ListingListCreateView.as_view(), name="listing-list"),
    path("listings/<int:listing_id>", views.ListingDetailView.as_view(), name="listing-detail"),
    path(
        "listings/<int:listing_id>/publish",
        views.ListingPublishView.as_view(),
        name="listing-publish",
    ),
    path(
        "listings/<int:listing_id>/offers",
        views.ListingOffersForSellerView.as_view(),
        name="listing-offers",
    ),
    path(
        "listings/<int:listing_id>/cancel",
        views.ListingCancelView.as_view(),
        name="listing-cancel",
    ),
    path(
        "listings/<int:listing_id>/relist",
        views.ListingRelistView.as_view(),
        name="listing-relist",
    ),
    path(
        "listings/<int:listing_id>/photos",
        views.ListingPhotosView.as_view(),
        name="listing-photos",
    ),
    path(
        "listings/<int:listing_id>/photos/<int:photo_id>",
        views.ListingPhotoDetailView.as_view(),
        name="listing-photo-detail",
    ),
]
