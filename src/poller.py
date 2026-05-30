"""
Weather poller that continuously fetches data from Open-Meteo API.
Handles retries, deduplication, and structured logging.
"""
import httpx
import logging
import time
from typing import Dict, Optional, Any
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# Open-Meteo API configuration
API_BASE = "https://api.open-meteo.com/v1/forecast"

CITIES = {
    "Ottawa": {"lat": 45.42, "lon": -75.69},
    "Toronto": {"lat": 43.70, "lon": -79.42},
    "Vancouver": {"lat": 49.25, "lon": -123.12}
}

WEATHER_PARAMS = (
    "temperature_2m,apparent_temperature,"
    "precipitation,wind_speed_10m,weather_code"
)


class WeatherPoller:
    """Polls Open-Meteo API for current weather conditions."""
    
    def __init__(self, db, detector, poll_interval: int = 300, max_retries: int = 3):
        """
        Initialize poller.
        
        Args:
            db: Database instance
            detector: Event detector instance
            poll_interval: Seconds between polls (default 5 minutes)
            max_retries: Max retries on failed fetch
        """
        self.db = db
        self.detector = detector
        self.poll_interval = poll_interval
        self.max_retries = max_retries
        self.last_timestamps = {}  # Track last timestamp per city to detect duplicates
    
    def fetch_city_weather(self, city: str, coords: Dict[str, float]) -> Optional[Dict[str, Any]]:
        """
        Fetch weather for a single city.
        
        Returns:
            Weather data dict or None if fetch failed
        """
        params = {
            "latitude": coords["lat"],
            "longitude": coords["lon"],
            "current": WEATHER_PARAMS,
            "wind_speed_unit": "kmh",
            "timezone": "auto"
        }
        
        # Three retries with exponential backoff. Open-Meteo is generally reliable
        # but occasionally slow. Most transient failures resolve within a few seconds.
        # After 3 failures we give up on that city for this cycle and try again next poll.
        # Chose not to alert on single-city failures — too noisy for a free API.
        for attempt in range(1, self.max_retries + 1):
            try:
                response = httpx.get(API_BASE, params=params, timeout=10)
                response.raise_for_status()
                
                data = response.json()
                current = data.get("current", {})
                
                # Standardize the response
                # Open-Meteo returns time as ISO string, convert to Unix timestamp (integer)
                iso_time = current.get("time")
                timestamp = int(datetime.fromisoformat(iso_time).timestamp()) if iso_time else None
                
                reading = {
                    "timestamp": timestamp,
                    "temperature_2m": current.get("temperature_2m"),
                    "apparent_temperature": current.get("apparent_temperature"),
                    "precipitation": current.get("precipitation"),
                    "wind_speed_10m": current.get("wind_speed_10m"),
                    "weather_code": current.get("weather_code")
                }
                
                return reading
                
            except httpx.TimeoutException:
                logger.warning(
                    f"Timeout fetching {city}. Attempt {attempt}/{self.max_retries}"
                )
            except httpx.HTTPStatusError as e:
                logger.warning(
                    f"HTTP {e.response.status_code} fetching {city}. "
                    f"Attempt {attempt}/{self.max_retries}"
                )
            except Exception as e:
                logger.warning(
                    f"Error fetching {city}: {e}. Attempt {attempt}/{self.max_retries}"
                )
            
            if attempt < self.max_retries:
                time.sleep(2 ** attempt)  # Exponential backoff
        
        logger.error(f"Failed to fetch weather for {city} after {self.max_retries} retries")
        return None
    
    def poll_once(self):
        """Single poll cycle for all cities."""
        logger.info("Starting poll cycle")
        
        for city, coords in CITIES.items():
            weather = self.fetch_city_weather(city, coords)
            
            if not weather:
                continue
            
            # Check for duplicate (same timestamp as last fetch for this city)
            if city in self.last_timestamps and self.last_timestamps[city] == weather["timestamp"]:
                logger.debug(f"Duplicate reading for {city}, skipping")
                continue
            
            # Store reading in database
            if self.db.store_reading(city, weather):
                self.last_timestamps[city] = weather["timestamp"]
                logger.info(f"Stored reading for {city}: temp={weather['temperature_2m']}°C")
                
                # Run event detection on this new reading
                self.detector.detect_events(city, weather)
    
    def run_continuous(self):
        """Run polling loop indefinitely."""
        logger.info(f"Poller starting (interval={self.poll_interval}s)")
        
        while True:
            try:
                self.poll_once()
            except Exception as e:
                logger.error(f"Unhandled error in poll cycle: {e}", exc_info=True)
            
            time.sleep(self.poll_interval)
