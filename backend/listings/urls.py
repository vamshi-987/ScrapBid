from django.urls import path

from listings import views

urlpatterns = [
    path("categories", views.CategoryListView.as_view(), name="category-list"),
]
