from django.urls import path

from listings import views

urlpatterns = [
    path("categories", views.CategoryListView.as_view(), name="category-list"),
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
