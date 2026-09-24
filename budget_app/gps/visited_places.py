from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests
import streamlit as st

# The tracker covers one phone (daniloustimenko@gmail.com's own), with no
# per-owner scoping on the API side — gating this feature to that email is
# the caller's job, not something this module can enforce.
LOCAL_TZ = ZoneInfo("America/Toronto")


def day_bounds_ms(day) -> tuple[int, int]:
    """Epoch-millisecond [start, end], inclusive, for one local calendar day —
    matches the API's arrival_time filter semantics. Computed in LOCAL_TZ so
    a transaction dated Sep 12 pulls the places visited on Sep 12 local time,
    not whatever UTC's Sep 12 happens to overlap."""
    local_midnight = datetime(day.year, day.month, day.day, tzinfo=LOCAL_TZ)
    start = local_midnight
    end = local_midnight + timedelta(days=1) - timedelta(milliseconds=1)
    return int(start.timestamp() * 1000), int(end.timestamp() * 1000)


def fetch_visited_places(start_ms: int, end_ms: int) -> list[dict]:
    """Raises requests.RequestException on failure — left to the caller,
    since the tracker being offline (a personal ngrok tunnel) is an expected
    condition worth showing the user, not a bug to hide behind a generic
    error boundary."""
    config = st.secrets["gps_tracking"]
    response = requests.get(
        f"{config['base_url']}/api/v1/visited-places",
        params={"start": start_ms, "end": end_ms},
        headers={"X-API-Key": config["api_key"]},
        timeout=10,
    )
    response.raise_for_status()
    return response.json()


def format_local_time(epoch_ms: int) -> str:
    return datetime.fromtimestamp(epoch_ms / 1000, tz=LOCAL_TZ).strftime("%-I:%M %p")
