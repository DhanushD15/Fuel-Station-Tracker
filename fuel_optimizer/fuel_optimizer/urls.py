from django.contrib import admin
from django.urls import path

from routing.views import map_view, route_distance

urlpatterns = [
    path("admin/", admin.site.urls),
    path("route/", route_distance, name="route_distance"),
    path("map/", map_view, name="map_view"),
]
