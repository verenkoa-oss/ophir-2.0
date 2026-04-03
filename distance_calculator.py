"""
OPHIR 2.0 | Distance Calculator
Calculates aircraft distance from RSSI using the Friis transmission equation.
Falls back to bearing estimation when coordinates are unavailable.
"""

import math
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))

import config

# ADS-B transmit power (typical Mode-S transponder)
_TRANSPONDER_TX_POWER_DBM = 54.0   # ~250 W EIRP
_ADS_B_FREQ_MHZ = 1090.0           # MHz
_SPEED_OF_LIGHT = 3e8              # m/s


def friis_path_loss(distance_km: float, frequency_mhz: float = _ADS_B_FREQ_MHZ) -> float:
    """Return free-space path loss in dB for a given distance and frequency."""
    if distance_km <= 0:
        return 0.0
    wavelength = _SPEED_OF_LIGHT / (frequency_mhz * 1e6)
    d_m = distance_km * 1000.0
    loss_db = 20 * math.log10(4 * math.pi * d_m / wavelength)
    return loss_db


def rssi_to_distance_km(
    rssi_dbm: float,
    tx_power_dbm: float = _TRANSPONDER_TX_POWER_DBM,
    frequency_mhz: float = _ADS_B_FREQ_MHZ,
    rx_gain_db: float = 0.0,
) -> float:
    """Estimate distance in km from received signal strength.

    Formula (Friis):  RSSI = Pt + Gr - FSPL
    → FSPL = Pt + Gr - RSSI
    → d = (λ / 4π) * 10^(FSPL/20)
    """
    fspl_db = tx_power_dbm + rx_gain_db - rssi_dbm
    wavelength = _SPEED_OF_LIGHT / (frequency_mhz * 1e6)
    d_m = (wavelength / (4 * math.pi)) * (10 ** (fspl_db / 20))
    return max(0.0, round(d_m / 1000.0, 2))


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in km between two lat/lon points."""
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    return R * 2 * math.asin(math.sqrt(a))


def bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Initial bearing (degrees true N) from point 1 to point 2."""
    lat1r = math.radians(lat1)
    lat2r = math.radians(lat2)
    dlon = math.radians(lon2 - lon1)
    x = math.sin(dlon) * math.cos(lat2r)
    y = math.cos(lat1r) * math.sin(lat2r) - math.sin(lat1r) * math.cos(lat2r) * math.cos(dlon)
    return (math.degrees(math.atan2(x, y)) + 360) % 360


def estimate_aircraft_distance(aircraft: dict) -> dict:
    """Return distance and bearing for an aircraft dict.

    If the aircraft has GPS coordinates the exact haversine distance is used;
    otherwise the RSSI-based Friis estimate is returned.
    """
    obs_lat = config.OBSERVER_LATITUDE
    obs_lon = config.OBSERVER_LONGITUDE

    lat = aircraft.get("latitude") or aircraft.get("lat")
    lon = aircraft.get("longitude") or aircraft.get("lon")
    rssi = aircraft.get("rssi")

    result: dict = {
        "observer_lat": obs_lat,
        "observer_lon": obs_lon,
        "method": "unknown",
        "distance_km": None,
        "bearing_deg": None,
    }

    if lat is not None and lon is not None:
        try:
            lat_f = float(lat)
            lon_f = float(lon)
            result["distance_km"] = round(haversine_km(obs_lat, obs_lon, lat_f, lon_f), 2)
            result["bearing_deg"] = round(bearing_deg(obs_lat, obs_lon, lat_f, lon_f), 1)
            result["method"] = "gps"
        except (TypeError, ValueError):
            pass

    if result["method"] == "unknown" and rssi is not None:
        try:
            rssi_f = float(rssi)
            # The RSSI from dump1090 already reflects receiver processing.
            # Use rx_gain_db=0 — the raw Friis equation without double-counting SDR gain.
            result["distance_km"] = rssi_to_distance_km(rssi_f, rx_gain_db=0.0)
            result["method"] = "rssi_friis"
        except (TypeError, ValueError):
            pass

    return result


if __name__ == "__main__":
    # Quick self-test
    print("=== Distance Calculator Self-Test ===")
    print(f"Observer: {config.OBSERVER_LATITUDE}°N, {config.OBSERVER_LONGITUDE}°E")

    # GPS test: aircraft over Tel Aviv
    ac_gps = {"latitude": 32.08, "longitude": 34.78}
    r = estimate_aircraft_distance(ac_gps)
    print(f"GPS test (Tel Aviv): {r['distance_km']} km, bearing {r['bearing_deg']}° — method={r['method']}")

    # RSSI test
    ac_rssi = {"rssi": -72}
    r2 = estimate_aircraft_distance(ac_rssi)
    print(f"RSSI test (-72 dBm): {r2['distance_km']} km — method={r2['method']}")
