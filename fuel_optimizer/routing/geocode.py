import os
import time

import requests

API_URL = "https://api.opencagedata.com/geocode/v1/json"
API_KEY = os.environ.get("OPENCAGE_KEY", "6a59054641044f72a30d8bca0577ee1c")
BATCH_LIMIT = int(os.environ.get("GEOCODE_BATCH_LIMIT", "0"))
SLEEP_SECONDS = float(os.environ.get("GEOCODE_SLEEP", "1.0"))


def run_geocode_batch():
    from routing.models import FuelStation

    queryset = (
        FuelStation.objects.filter(latitude__isnull=True, longitude__isnull=True)
        .exclude(address__exact="")
        .order_by("id")
    )

    total = queryset.count()
    if total == 0:
        print("No stations are missing coordinates.")
        return

    print(f"Geocoding {total} stations (limit={BATCH_LIMIT or 'all'})")
    processed = 0

    for station in queryset.iterator():
        if BATCH_LIMIT and processed >= BATCH_LIMIT:
            break

        address = ", ".join(
            part for part in [station.address, station.city, station.state, "USA"] if part
        )
        params = {"q": address, "key": API_KEY, "limit": 1, "countrycode": "us"}

        try:
            response = requests.get(API_URL, params=params, timeout=8)
            response.raise_for_status()
            payload = response.json()
        except requests.RequestException as exc:
            print(f"Request error for id={station.id}: {exc}")
            time.sleep(SLEEP_SECONDS)
            continue
        except ValueError:
            print(f"Invalid JSON for id={station.id}")
            time.sleep(SLEEP_SECONDS)
            continue

        results = payload.get("results") or []
        if not results:
            print(f"No geocode result for id={station.id}: {address}")
            time.sleep(SLEEP_SECONDS)
            continue

        geometry = results[0].get("geometry") or {}
        lat = geometry.get("lat")
        lon = geometry.get("lng")
        if lat is None or lon is None:
            print(f"No coordinates in geocode response for id={station.id}")
            time.sleep(SLEEP_SECONDS)
            continue

        station.latitude = lat
        station.longitude = lon
        station.save(update_fields=["latitude", "longitude"])
        processed += 1
        print(f"Updated id={station.id}: ({lat}, {lon})")
        time.sleep(SLEEP_SECONDS)

    print(f"Finished. Updated {processed} station(s).")


if __name__ == "__main__":
    import django

    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "fuel_optimizer.settings")
    django.setup()
    run_geocode_batch()
