import json
import math
from functools import lru_cache
from urllib.parse import quote_plus

from django.core.cache import cache
from django.http import JsonResponse
from django.shortcuts import render

from .models import FuelStation
from .services import geocode_address_opencage, get_route

MPG = 10.0
MAX_RANGE_MILES = 500.0
CORRIDOR_RADIUS_MILES = 25.0
WAYPOINT_WINDOWS_MILES = (60.0, 120.0, 200.0, 320.0)
DETOUR_WEIGHT = 0.04
WAYPOINT_WEIGHT = 0.002


class TripPlanningError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def _parse_body(request):
    if not request.body:
        return {}
    try:
        data = json.loads(request.body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _parse_bool(value, default=False):
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y"}:
        return True
    if text in {"0", "false", "no", "n"}:
        return False
    return default


def _parse_coords(value):
    if value is None:
        return None
    if isinstance(value, (list, tuple)) and len(value) >= 2:
        try:
            lon = float(value[0])
            lat = float(value[1])
            return [lon, lat]
        except (TypeError, ValueError):
            return None

    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            parsed = None
        if parsed is not None:
            return _parse_coords(parsed)
        if "," in text:
            parts = [p.strip() for p in text.split(",")]
            if len(parts) >= 2:
                try:
                    a = float(parts[0])
                    b = float(parts[1])
                except ValueError:
                    return None
                if abs(a) > 90 and abs(b) <= 90:
                    return [a, b]
                if abs(b) > 90 and abs(a) <= 90:
                    return [b, a]
                return [a, b]
    return None


def _is_probably_us(lon, lat):
    return -179.9 <= lon <= -66.0 and 18.0 <= lat <= 72.0


def _haversine_miles(lat1, lon1, lat2, lon2):
    radius_miles = 3958.7613
    lat1_rad = math.radians(lat1)
    lon1_rad = math.radians(lon1)
    lat2_rad = math.radians(lat2)
    lon2_rad = math.radians(lon2)
    dlat = lat2_rad - lat1_rad
    dlon = lon2_rad - lon1_rad
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(dlon / 2) ** 2
    )
    return 2 * radius_miles * math.asin(math.sqrt(a))


def _build_cumulative_miles(route_points):
    if not route_points:
        return [0.0]
    cumulative = [0.0]
    for idx in range(1, len(route_points)):
        lat1, lon1 = route_points[idx - 1]
        lat2, lon2 = route_points[idx]
        cumulative.append(cumulative[-1] + _haversine_miles(lat1, lon1, lat2, lon2))
    return cumulative


def _route_bbox(route_points, padding_miles):
    lats = [p[0] for p in route_points]
    lons = [p[1] for p in route_points]
    lat_pad = padding_miles / 69.0
    mid_lat = (min(lats) + max(lats)) / 2.0
    lon_denominator = 69.172 * max(0.2, abs(math.cos(math.radians(mid_lat))))
    lon_pad = padding_miles / lon_denominator
    return (
        min(lats) - lat_pad,
        max(lats) + lat_pad,
        min(lons) - lon_pad,
        max(lons) + lon_pad,
    )


@lru_cache(maxsize=1)
def _load_station_rows():
    rows = FuelStation.objects.filter(
        latitude__isnull=False, longitude__isnull=False
    ).values("name", "city", "state", "retail_price", "latitude", "longitude")
    result = []
    for row in rows:
        try:
            result.append(
                {
                    "name": row["name"],
                    "city": row["city"],
                    "state": row["state"],
                    "price": float(row["retail_price"]),
                    "latitude": float(row["latitude"]),
                    "longitude": float(row["longitude"]),
                }
            )
        except (TypeError, ValueError):
            continue
    return result


def _map_stations_to_route(route_points, cumulative):
    if not route_points:
        return []
    lat_min, lat_max, lon_min, lon_max = _route_bbox(
        route_points, CORRIDOR_RADIUS_MILES + 20.0
    )
    candidates = [
        station
        for station in _load_station_rows()
        if lat_min <= station["latitude"] <= lat_max
        and lon_min <= station["longitude"] <= lon_max
    ]
    if not candidates:
        return []

    stride = max(1, len(route_points) // 800)
    sampled_indexes = list(range(0, len(route_points), stride))
    if sampled_indexes[-1] != len(route_points) - 1:
        sampled_indexes.append(len(route_points) - 1)

    mapped = []
    for station in candidates:
        best_distance = float("inf")
        best_index = 0
        for idx in sampled_indexes:
            route_lat, route_lon = route_points[idx]
            distance = _haversine_miles(
                station["latitude"], station["longitude"], route_lat, route_lon
            )
            if distance < best_distance:
                best_distance = distance
                best_index = idx
        if best_distance <= CORRIDOR_RADIUS_MILES:
            mapped.append(
                {
                    **station,
                    "route_mile": cumulative[best_index],
                    "detour_miles": best_distance,
                }
            )
    return mapped


def _build_waypoint_markers(total_miles, start_with_full_tank):
    if total_miles <= 0:
        return []
    marker = MAX_RANGE_MILES if start_with_full_tank else 0.0
    markers = []
    while marker < total_miles:
        markers.append(marker)
        marker += MAX_RANGE_MILES
    return markers


def _score_station(station, marker):
    return (
        station["price"]
        + station["detour_miles"] * DETOUR_WEIGHT
        + abs(station["route_mile"] - marker) * WAYPOINT_WEIGHT
    )


def _select_station(mapped_stations, marker, previous_marker):
    if not mapped_stations:
        return None
    for window in WAYPOINT_WINDOWS_MILES:
        candidates = [
            station
            for station in mapped_stations
            if abs(station["route_mile"] - marker) <= window
            and station["route_mile"] >= previous_marker - 40.0
        ]
        if candidates:
            return min(candidates, key=lambda station: _score_station(station, marker))

    candidates = [
        station
        for station in mapped_stations
        if station["route_mile"] >= previous_marker - 40.0
    ]
    if not candidates:
        candidates = mapped_stations
    return min(candidates, key=lambda station: _score_station(station, marker))


def _resolve_location(raw_value):
    coords = _parse_coords(raw_value)
    if coords:
        return coords
    if not isinstance(raw_value, str) or not raw_value.strip():
        return None
    return geocode_address_opencage(raw_value)


def _to_route_points(decoded_points, start_coords, finish_coords):
    if decoded_points:
        return [(float(lat), float(lon)) for lat, lon in decoded_points]
    return [
        (float(start_coords[1]), float(start_coords[0])),
        (float(finish_coords[1]), float(finish_coords[0])),
    ]


def _trip_cache_key(start_raw, finish_raw, start_with_full_tank):
    start_key = json.dumps(start_raw, sort_keys=True) if not isinstance(start_raw, str) else start_raw.strip()
    finish_key = (
        json.dumps(finish_raw, sort_keys=True)
        if not isinstance(finish_raw, str)
        else finish_raw.strip()
    )
    return f"trip:v2:{start_key}:{finish_key}:{int(start_with_full_tank)}"


def _build_start_end_map_urls(start_coords, finish_coords):
    start_lat, start_lon = float(start_coords[1]), float(start_coords[0])
    end_lat, end_lon = float(finish_coords[1]), float(finish_coords[0])
    return {
        "openstreetmap_directions": (
            "https://www.openstreetmap.org/directions"
            f"?engine=fossgis_osrm_car&route={start_lat}%2C{start_lon}%3B{end_lat}%2C{end_lon}"
        ),
        "google_maps_directions": (
            "https://www.google.com/maps/dir/?api=1"
            f"&origin={quote_plus(f'{start_lat},{start_lon}')}"
            f"&destination={quote_plus(f'{end_lat},{end_lon}')}"
            "&travelmode=driving"
        ),
    }


def _build_trip_plan(start_raw, finish_raw, start_with_full_tank):
    start_coords = _resolve_location(start_raw)
    finish_coords = _resolve_location(finish_raw)
    if not start_coords or not finish_coords:
        raise TripPlanningError(
            "Invalid start or finish. Use US address text or coordinates [lon, lat].", 400
        )

    if not _is_probably_us(start_coords[0], start_coords[1]) or not _is_probably_us(
        finish_coords[0], finish_coords[1]
    ):
        raise TripPlanningError(
            "Start and finish must be locations within the USA.", 400
        )

    distance_meters, encoded_polyline, decoded_route = get_route(start_coords, finish_coords)
    if distance_meters is None:
        raise TripPlanningError(
            "Route service failed. Check API key/quota and try again.", 502
        )

    route_points = _to_route_points(decoded_route, start_coords, finish_coords)
    cumulative_miles = _build_cumulative_miles(route_points)
    total_distance_miles = (
        cumulative_miles[-1] if cumulative_miles else float(distance_meters) / 1609.344
    )

    mapped_stations = _map_stations_to_route(route_points, cumulative_miles)
    waypoints = _build_waypoint_markers(total_distance_miles, start_with_full_tank)

    fuel_stops = []
    total_cost = 0.0
    previous_marker = -MAX_RANGE_MILES
    missing_station_markers = []

    for order, marker in enumerate(waypoints, start=1):
        segment_distance = min(MAX_RANGE_MILES, total_distance_miles - marker)
        if segment_distance <= 0:
            continue

        station = _select_station(mapped_stations, marker, previous_marker)
        if not station:
            missing_station_markers.append(round(marker, 2))
            fuel_stops.append(
                {
                    "order": order,
                    "route_mile_marker": round(marker, 2),
                    "segment_distance_miles": round(segment_distance, 2),
                    "note": "No geocoded station found near this route segment.",
                }
            )
            previous_marker = marker
            continue

        gallons = segment_distance / MPG
        segment_cost = gallons * station["price"]
        total_cost += segment_cost
        previous_marker = marker

        fuel_stops.append(
            {
                "order": order,
                "route_mile_marker": round(marker, 2),
                "segment_distance_miles": round(segment_distance, 2),
                "name": station["name"],
                "city": station["city"],
                "state": station["state"],
                "latitude": station["latitude"],
                "longitude": station["longitude"],
                "distance_to_route_miles": round(station["detour_miles"], 2),
                "price_per_gallon_usd": round(station["price"], 3),
                "gallons_purchased": round(gallons, 2),
                "segment_cost_usd": round(segment_cost, 2),
            }
        )

    purchasable_distance = max(
        total_distance_miles - (MAX_RANGE_MILES if start_with_full_tank else 0.0), 0.0
    )
    total_purchased_gallons = purchasable_distance / MPG
    start_link = (
        start_raw if isinstance(start_raw, str) else f"{start_coords[0]},{start_coords[1]}"
    )
    finish_link = (
        finish_raw
        if isinstance(finish_raw, str)
        else f"{finish_coords[0]},{finish_coords[1]}"
    )

    plan = {
        "start": {
            "input": start_raw,
            "coordinates": {"lon": start_coords[0], "lat": start_coords[1]},
        },
        "finish": {
            "input": finish_raw,
            "coordinates": {"lon": finish_coords[0], "lat": finish_coords[1]},
        },
        "assumptions": {
            "vehicle_range_miles": MAX_RANGE_MILES,
            "mpg": MPG,
            "start_with_full_tank": start_with_full_tank,
            "corridor_radius_miles": CORRIDOR_RADIUS_MILES,
        },
        "summary": {
            "total_distance_miles": round(total_distance_miles, 2),
            "total_fuel_consumed_gallons": round(total_distance_miles / MPG, 2),
            "total_fuel_purchased_gallons": round(total_purchased_gallons, 2),
            "total_fuel_cost_usd": round(total_cost, 2),
            "number_of_fuel_stops": sum(1 for stop in fuel_stops if "name" in stop),
        },
        "fuel_stops": fuel_stops,
        "route_polyline": encoded_polyline,
        "start_end_map": _build_start_end_map_urls(start_coords, finish_coords),
        "map_url": (
            f"/map/?start={quote_plus(str(start_link))}"
            f"&finish={quote_plus(str(finish_link))}"
            f"&start_with_full_tank={'true' if start_with_full_tank else 'false'}"
        ),
    }

    if missing_station_markers:
        plan["warnings"] = {
            "missing_station_markers_miles": missing_station_markers,
            "message": "Some stops were estimated because no geocoded station was found nearby.",
        }
    return plan


def _extract_inputs(request):
    payload = _parse_body(request)
    start = request.GET.get("start", payload.get("start") or payload.get("start_coords"))
    finish = request.GET.get(
        "finish",
        payload.get("finish") or payload.get("end") or payload.get("end_coords"),
    )
    start_with_full_tank = _parse_bool(
        request.GET.get(
            "start_with_full_tank", payload.get("start_with_full_tank", False)
        )
    )
    return start, finish, start_with_full_tank


def route_distance(request):
    start, finish, start_with_full_tank = _extract_inputs(request)
    if start is None or finish is None:
        return JsonResponse(
            {
                "error": "Both start and finish are required.",
                "example": {
                    "start": "Los Angeles, CA",
                    "finish": "Dallas, TX",
                    "start_with_full_tank": False,
                },
            },
            status=400,
        )

    cache_key = _trip_cache_key(start, finish, start_with_full_tank)
    cached = cache.get(cache_key)
    if cached is not None:
        public_payload = dict(cached)
        public_payload.pop("route_polyline", None)
        return JsonResponse(public_payload)

    try:
        plan = _build_trip_plan(start, finish, start_with_full_tank)
    except TripPlanningError as exc:
        return JsonResponse({"error": str(exc)}, status=exc.status)

    cache.set(cache_key, plan, timeout=60 * 15)
    public_payload = dict(plan)
    public_payload.pop("route_polyline", None)
    return JsonResponse(public_payload)


def map_view(request):
    start, finish, start_with_full_tank = _extract_inputs(request)
    if start is None or finish is None:
        return JsonResponse(
            {"error": "Both start and finish are required for map view."}, status=400
        )

    try:
        plan = _build_trip_plan(start, finish, start_with_full_tank)
    except TripPlanningError as exc:
        return JsonResponse({"error": str(exc)}, status=exc.status)

    map_stops = [
        {
            "name": stop["name"],
            "city": stop["city"],
            "state": stop["state"],
            "latitude": stop["latitude"],
            "longitude": stop["longitude"],
            "price": stop["price_per_gallon_usd"],
        }
        for stop in plan["fuel_stops"]
        if "name" in stop
    ]

    return render(
        request,
        "routing/map.html",
        {
            "polyline": plan.get("route_polyline") or "",
            "fuel_stops": json.dumps(map_stops),
            "start_coords": json.dumps(
                [
                    plan["start"]["coordinates"]["lon"],
                    plan["start"]["coordinates"]["lat"],
                ]
            ),
            "end_coords": json.dumps(
                [
                    plan["finish"]["coordinates"]["lon"],
                    plan["finish"]["coordinates"]["lat"],
                ]
            ),
        },
    )
