"""
OPHIR Distance Calculator
Estimates aircraft range from received RSSI using the Friis transmission
equation for 1090 MHz ADS-B signals.

When a civilian aircraft provides GPS coordinates the real Euclidean (great-
circle) distance is used directly; for aircraft that do *not* transmit GPS the
RSSI-based Friis estimate is returned instead.
"""

import math
import logging
import config

logger = logging.getLogger(__name__)

# Speed of light (m/s)
_C = 299_792_458.0


def _free_space_path_loss_db(distance_km: float, freq_hz: float) -> float:
    """Calculate FSPL (dB) for *distance_km* at *freq_hz*."""
    d_m = distance_km * 1000.0
    fspl = 20 * math.log10(d_m) + 20 * math.log10(freq_hz) + 20 * math.log10(4 * math.pi / _C)
    return fspl


def rssi_to_distance_km(
    rssi_dbm: float,
    tx_power_dbm: float = config.DISTANCE_TX_POWER_DBM,
    tx_gain_dbi: float = config.DISTANCE_TX_GAIN_DBI,
    rx_gain_dbi: float = config.DISTANCE_RX_GAIN_DBI,
    freq_hz: float = config.DISTANCE_FREQ_HZ,
) -> float:
    """Convert received RSSI (dBm) to an estimated slant-range distance (km).

    Uses the Friis transmission equation rearranged for distance:
        FSPL = Pt + Gt + Gr - Pr
        d = (λ / 4π) × 10^((Pt + Gt + Gr - Pr) / 20)

    Parameters
    ----------
    rssi_dbm:
        Received signal strength in dBm.
    tx_power_dbm:
        Transmitter power (dBm); default from config.
    tx_gain_dbi:
        Transmitter antenna gain (dBi); default from config.
    rx_gain_dbi:
        Receiver antenna gain (dBi); default from config.
    freq_hz:
        Carrier frequency (Hz); default 1090 MHz.

    Returns
    -------
    float
        Estimated distance in km, clamped to [DISTANCE_MIN_KM, DISTANCE_MAX_KM].
    """
    wavelength_m = _C / freq_hz
    fspl_db = tx_power_dbm + tx_gain_dbi + rx_gain_dbi - rssi_dbm

    # d = (λ / 4π) × 10^(FSPL_dB / 20)
    distance_m = (wavelength_m / (4 * math.pi)) * (10 ** (fspl_db / 20.0))
    distance_km = distance_m / 1000.0

    clamped = max(config.DISTANCE_MIN_KM, min(config.DISTANCE_MAX_KM, distance_km))
    if clamped != distance_km:
        logger.debug(
            f"Distance clamped from {distance_km:.1f} km to {clamped:.1f} km "
            f"(rssi={rssi_dbm} dBm)"
        )
    return round(clamped, 2)


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Calculate great-circle distance between two points (km)."""
    R = 6371.0
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    return round(R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a)), 2)


def estimate_distance(
    rssi_dbm: float | None,
    aircraft_lat: float | None,
    aircraft_lon: float | None,
    observer_lat: float = config.OBSERVER_LAT,
    observer_lon: float = config.OBSERVER_LON,
) -> dict:
    """Return the best distance estimate for an aircraft.

    If the aircraft has GPS coordinates the real great-circle distance is used.
    Otherwise the RSSI-based Friis estimate is returned.

    Returns
    -------
    dict with keys:
        distance_km   – estimated distance (float or None)
        method        – "gps" | "rssi" | "unknown"
        rssi_dbm      – echo of the input RSSI (float or None)
    """
    # GPS distance (preferred)
    if aircraft_lat is not None and aircraft_lon is not None:
        dist = haversine_km(observer_lat, observer_lon, aircraft_lat, aircraft_lon)
        return {"distance_km": dist, "method": "gps", "rssi_dbm": rssi_dbm}

    # RSSI-based fallback
    if rssi_dbm is not None:
        dist = rssi_to_distance_km(rssi_dbm)
        return {"distance_km": dist, "method": "rssi", "rssi_dbm": rssi_dbm}

    return {"distance_km": None, "method": "unknown", "rssi_dbm": None}
