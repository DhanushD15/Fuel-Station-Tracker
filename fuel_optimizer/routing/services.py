import os

import polyline as pl
import requests

ORS_API_URL = "https://api.openrouteservice.org/v2/directions/driving-car"
OPENCAGE_API_URL = "https://api.opencagedata.com/geocode/v1/json"

OPENROUTESERVICE_API_KEY = os.environ.get(
    "OPENROUTESERVICE_API_KEY",
    "eyJvcmciOiI1YjNjZTM1OTc4NTExMTAwMDFjZjYyNDgiLCJpZCI6ImY1YzczZjUzOWFlMTQzNWE4NTUwZTQyMDE5YWFhNGRiIiwiaCI6Im11cm11cjY0In0=",
)
OPENCAGE_API_KEY = os.environ.get(
    "OPENCAGE_KEY", "6a59054641044f72a30d8bca0577ee1c"
)


def get_route(start_coords, end_coords):
    """Return (distance_meters, encoded_polyline, decoded_points) or (None, None, None)."""
    if not OPENROUTESERVICE_API_KEY:
        return None, None, None
    try:
        start_lon, start_lat = float(start_coords[0]), float(start_coords[1])
        end_lon, end_lat = float(end_coords[0]), float(end_coords[1])
    except (TypeError, ValueError, IndexError):
        return None, None, None

    headers = {
        "Authorization": OPENROUTESERVICE_API_KEY,
        "Content-Type": "application/json",
    }
    payload = {"coordinates": [[start_lon, start_lat], [end_lon, end_lat]]}

    try:
        response = requests.post(
            ORS_API_URL, json=payload, headers=headers, timeout=20
        )
        response.raise_for_status()
        data = response.json()
    except requests.RequestException:
        return None, None, None
    except ValueError:
        return None, None, None

    routes = data.get("routes") or []
    if not routes:
        return None, None, None

    route = routes[0]
    summary = route.get("summary") or {}
    distance_meters = summary.get("distance")
    geometry = route.get("geometry")

    decoded = []
    if geometry:
        try:
            decoded = pl.decode(geometry)
        except (TypeError, ValueError):
            decoded = []
    return distance_meters, geometry, decoded


def geocode_address_opencage(address):
    """Return [lon, lat] for a US location string, or None."""
    if not OPENCAGE_API_KEY:
        return None
    if not isinstance(address, str) or not address.strip():
        return None
    params = {
        "q": address.strip(),
        "key": OPENCAGE_API_KEY,
        "limit": 1,
        "countrycode": "us",
    }
    try:
        response = requests.get(OPENCAGE_API_URL, params=params, timeout=8)
        response.raise_for_status()
        payload = response.json()
    except requests.RequestException:
        return None
    except ValueError:
        return None

    results = payload.get("results") or []
    if not results:
        return None

    geometry = results[0].get("geometry") or {}
    lat = geometry.get("lat")
    lon = geometry.get("lng")
    if lat is None or lon is None:
        return None
    try:
        return [float(lon), float(lat)]
    except (TypeError, ValueError):
        return None
