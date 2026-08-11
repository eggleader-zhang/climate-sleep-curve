"""Constants for Climate Sleep Curve."""

from __future__ import annotations

DOMAIN = "climate_sleep_curve"
NAME = "Climate Sleep Curve"
VERSION = "0.1.0"
STORE_KEY = DOMAIN
STORE_VERSION = 1
PLATFORMS = ["sensor", "switch", "select", "button"]

MIN_DURATION_MINUTES = 240
MAX_DURATION_MINUTES = 720
MAX_POINTS = 25
MIN_TEMPERATURE_C = 5.0
MAX_TEMPERATURE_C = 40.0

SIGNAL_UPDATED = f"{DOMAIN}_updated"
SIGNAL_CONTROLLER_ADDED = f"{DOMAIN}_controller_added"

EVENT_SESSION_STARTED = f"{DOMAIN}_session_started"
EVENT_POINT_PROCESSED = f"{DOMAIN}_point_processed"
EVENT_SESSION_STOPPED = f"{DOMAIN}_session_stopped"
EVENT_SESSION_COMPLETED = f"{DOMAIN}_session_completed"
EVENT_ERROR = f"{DOMAIN}_error"

DEFAULT_SETTINGS = {
    "default_retry_count": 1,
    "default_retry_delay_seconds": 10,
    "history_retention_days": 30,
}

