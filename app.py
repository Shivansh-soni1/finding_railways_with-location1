from flask import Flask, request, render_template
from geopy.geocoders import Nominatim
import csv
import math
import time
import requests
import pandas as pd

app = Flask(__name__)

# ==================================================
# CONFIG
# ==================================================
API_URL = "https://api.railradar.org/api/v1/trains/between"
API_KEY = "rr_6ilyrx3lm3vewaa53nhqtzz81fjv4jmc"

geolocator = Nominatim(user_agent="railway_station_finder")

# ==================================================
# GEO CACHE
# ==================================================
geo_cache = {}

def get_lat_lon(place):
    if place in geo_cache:
        return geo_cache[place]

    try:
        loc = geolocator.geocode(place, timeout=10)
        time.sleep(1)
        if loc:
            geo_cache[place] = (loc.latitude, loc.longitude)
            return geo_cache[place]
    except:
        pass

    return None, None


# ==================================================
# HAVERSINE DISTANCE
# ==================================================
def haversine(lat1, lon1, lat2, lon2):
    R = 6371
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1))
        * math.cos(math.radians(lat2))
        * math.sin(dlon / 2) ** 2
    )
    return 2 * R * math.atan2(math.sqrt(a), math.sqrt(1 - a))


# ==================================================
# LOAD STATIONS FROM CSV
# CSV HEADER:
# s.no,code,Station,Latitude,Longitude
# ==================================================
def load_stations(csv_file):
    stations = []
    with open(csv_file, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for r in reader:
            try:
                stations.append({
                    "code": r["code"].strip(),
                    "name": r["Station"].strip(),
                    "lat": float(r["Latitude"]),
                    "lon": float(r["Longitude"])
                })
            except:
                continue
    return stations


small_stations = load_stations("Only_small.csv")
junction_stations = load_stations("mp_junction.csv")

print("Small stations:", len(small_stations))
print("Junction stations:", len(junction_stations))

if not small_stations or not junction_stations:
    raise RuntimeError("CSV files not loaded correctly")


# ==================================================
# FIND NEAREST STATION
# ==================================================
def find_nearest(lat, lon, station_list):
    nearest = None
    min_dist = float("inf")

    for s in station_list:
        dist = haversine(lat, lon, s["lat"], s["lon"])
        if dist < min_dist:
            min_dist = dist
            nearest = {
                "code": s["code"],
                "name": s["name"],
                "distance_km": round(dist, 2)
            }
    return nearest


# ==================================================
# ROUTES
# ==================================================
@app.route("/")
def index():
    return render_template("index.html")


@app.route("/results")
def results():
    from_place = request.args.get("from_place", "").strip()
    to_place   = request.args.get("to_place", "").strip()
    date       = request.args.get("date", "").strip()

    if not from_place or not to_place or not date:
        return "Invalid input", 400

    # -----------------------------
    # 1️⃣ Geocode places
    # -----------------------------
    from_lat, from_lon = get_lat_lon(from_place)
    to_lat, to_lon     = get_lat_lon(to_place)

    if not from_lat or not to_lat:
        return "Place not found", 400

    headers = {"X-API-Key": API_KEY}

    # -----------------------------
    # STEP 1: SMALL → SMALL
    # -----------------------------
    from_small = find_nearest(from_lat, from_lon, small_stations)
    to_small   = find_nearest(to_lat, to_lon, small_stations)

    params = {
        "from": from_small["code"],
        "to": to_small["code"],
        "date": date
    }

    res = requests.get(API_URL, headers=headers, params=params, timeout=15)
    data = res.json()
    trains = data.get("trains") or data.get("data", {}).get("trains", [])

    route_mode = "Direct (Small → Small)"
    from_station = from_small
    to_station   = to_small

    # -----------------------------
    # STEP 2: JUNCTION → SMALL
    # -----------------------------
    if not trains:
        from_junction = find_nearest(from_lat, from_lon, junction_stations)

        params = {
            "from": from_junction["code"],
            "to": to_small["code"],
            "date": date
        }

        res = requests.get(API_URL, headers=headers, params=params, timeout=15)
        data = res.json()
        trains = data.get("trains") or data.get("data", {}).get("trains", [])

        route_mode = "Via Junction (Junction → Small)"
        from_station = from_junction
        to_station   = to_small

    # -----------------------------
    # STEP 3: JUNCTION → JUNCTION
    # -----------------------------
    if not trains:
        from_junction = find_nearest(from_lat, from_lon, junction_stations)
        to_junction   = find_nearest(to_lat, to_lon, junction_stations)

        params = {
            "from": from_junction["code"],
            "to": to_junction["code"],
            "date": date
        }

        res = requests.get(API_URL, headers=headers, params=params, timeout=15)
        data = res.json()
        trains = data.get("trains") or data.get("data", {}).get("trains", [])

        route_mode = "Via Junctions (Junction → Junction)"
        from_station = from_junction
        to_station   = to_junction

    # -----------------------------
    # NO TRAINS FOUND
    # -----------------------------
    if not trains:
        return render_template(
            "results.html",
            from_station=from_station,
            to_station=to_station,
            trains=[],
            columns=[],
            total=0,
            route_mode=route_mode
        )

    # -----------------------------
    # RANK TRAINS
    # -----------------------------
    df = pd.json_normalize(trains).fillna(0)

    df["bestScore"] = (
        (df.get("avgSpeedKmph", 0) * 2)
        - (df.get("travelTimeMinutes", 0) * 0.01)
        - (df.get("totalHalts", 0) * 5)
        + (df.get("runningDays.allDays", 0) * 10)
    )

    df = df.sort_values("bestScore", ascending=False).reset_index(drop=True)
    df.insert(0, "Rank", df.index + 1)

    display_cols = [
        "Rank",
        "trainName",
        "travelTimeMinutes",
        "avgSpeedKmph",
        "totalHalts",
        "distanceKm",
        "bestScore"
    ]

    df = df[display_cols]

    return render_template(
        "results.html",
        from_station=from_station,
        to_station=to_station,
        trains=df.values.tolist(),
        columns=df.columns.tolist(),
        total=len(df),
        route_mode=route_mode
    )


# ==================================================
# RUN SERVER
# ==================================================
if __name__ == "__main__":
    app.run(debug=True)
