from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase

from .models import FuelStation
from . import views as routing_views

ROUTE_POINTS = [
    (35.0, -120.0),
    (35.0, -115.0),
    (35.0, -110.0),
    (35.0, -105.0),
    (35.0, -100.0),
    (35.0, -95.0),
    (35.0, -90.0),
]


class RouteApiTests(TestCase):
    def setUp(self):
        cache.clear()
        routing_views._load_station_rows.cache_clear()
        stations = [
            ("Start Fuel", "Bakersfield", "CA", 3.90, 35.0, -119.8),
            ("Mesa Fuel", "Flagstaff", "AZ", 3.10, 35.0, -110.3),
            ("Plains Fuel", "Amarillo", "TX", 3.00, 35.1, -101.0),
            ("Delta Fuel", "Memphis", "TN", 3.40, 35.0, -90.4),
        ]
        for idx, (name, city, state, price, lat, lon) in enumerate(stations, start=1):
            FuelStation.objects.create(
                truckstop_id=idx,
                name=name,
                address="Test",
                city=city,
                state=state,
                rack_id=idx,
                retail_price=price,
                latitude=lat,
                longitude=lon,
            )
        routing_views._load_station_rows.cache_clear()

    @patch("routing.views.get_route")
    def test_route_requires_inputs(self, mock_get_route):
        response = self.client.get("/route/")
        self.assertEqual(response.status_code, 400)
        self.assertIn("error", response.json())
        mock_get_route.assert_not_called()

    @patch("routing.views.get_route")
    def test_route_returns_stops_and_cost(self, mock_get_route):
        mock_get_route.return_value = (2700000, "encoded-polyline", ROUTE_POINTS)
        response = self.client.get("/route/", {"start": "-120,35", "finish": "-90,35"})
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn("summary", payload)
        self.assertGreater(payload["summary"]["number_of_fuel_stops"], 0)
        self.assertGreater(payload["summary"]["total_fuel_cost_usd"], 0)
        self.assertNotIn("route_polyline", payload)
        self.assertIn("start_end_map", payload)
        self.assertIn("openstreetmap_directions", payload["start_end_map"])
        self.assertIn("google_maps_directions", payload["start_end_map"])
        self.assertIn("map_url", payload)

    @patch("routing.views.get_route")
    def test_start_with_full_tank_reduces_stops(self, mock_get_route):
        mock_get_route.return_value = (2700000, "encoded-polyline", ROUTE_POINTS)
        default_response = self.client.get(
            "/route/", {"start": "-120,35", "finish": "-90,35"}
        )
        full_tank_response = self.client.get(
            "/route/",
            {"start": "-120,35", "finish": "-90,35", "start_with_full_tank": "true"},
        )
        default_stops = default_response.json()["summary"]["number_of_fuel_stops"]
        full_tank_stops = full_tank_response.json()["summary"]["number_of_fuel_stops"]
        self.assertGreater(default_stops, full_tank_stops)

    @patch("routing.views.get_route")
    def test_map_endpoint_renders(self, mock_get_route):
        mock_get_route.return_value = (2700000, "encoded-polyline", ROUTE_POINTS)
        response = self.client.get("/map/", {"start": "-120,35", "finish": "-90,35"})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Fuel Optimized Route Map")
