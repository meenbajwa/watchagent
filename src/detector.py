"""
Event detection for WatchAgent.

Each check function runs on every new reading. Most checks are stateless
(just look at current values) but rapid_temperature_change and 
weather_code_transition need the previous reading — if there's no history
for a city yet they return nothing, which is fine.

Thresholds are city-specific because the same temperature means different
things in different climates. Vancouver hitting 27°C is genuinely unusual;
Ottawa hitting 27°C in July is Tuesday.

One thing I'm not fully happy with: the weather_code transition dict only
catches 5 transitions. There are edge cases it misses (e.g. fog -> snow).
Left it here intentionally rather than over-engineering it.
"""
import logging
from typing import Dict, Any, Optional, List
from datetime import datetime

logger = logging.getLogger(__name__)

# City-specific temperature extremes (adjusted for local climate)
# These define what counts as "extreme" for each city's climate profile
CITY_TEMP_THRESHOLDS = {
    "Ottawa": {"extreme_high": 32, "extreme_low": -25, "unusual_change": 8},
    "Toronto": {"extreme_high": 31, "extreme_low": -23, "unusual_change": 8},
    "Vancouver": {"extreme_high": 26, "extreme_low": -5, "unusual_change": 6}
}

# Weather phenomena that are inherently "notable"
PRECIPITATION_THRESHOLD = 10  # mm/hour is heavy rain
WIND_THRESHOLD = {"Ottawa": 40, "Toronto": 40, "Vancouver": 35}  # km/h
WIND_WARNING_THRESHOLD = {"Ottawa": 60, "Toronto": 60, "Vancouver": 50}


class EventDetector:
    """Detects notable weather events from readings."""
    
    def __init__(self, db):
        """
        Initialize detector.
        
        Args:
            db: Database instance for querying historical data
        """
        self.db = db
    
    def detect_events(self, city: str, current_reading: Dict[str, Any]):
        """
        Detect events from a new reading and store them.
        
        This runs after each new reading is stored. It queries recent history
        to contextualize the new reading.
        """
        events = self._detect_events_internal(city, current_reading)
        
        # Store all detected events
        for event in events:
            self.db.store_event(
                city=city,
                event_type=event["type"],
                timestamp=current_reading["timestamp"],
                severity=event["severity"],
                description=event["description"],
                data=event.get("data")
            )
    
    def detect(self, city: str, current_reading: Dict[str, Any]) -> List[Dict]:
        """
        Detect events from a reading WITHOUT storing them.
        
        Used by analysis tools (replay_detection) that need to test what would
        have fired without modifying the database.
        
        Returns:
            List of event dicts with keys: type, severity, description, data
        """
        return self._detect_events_internal(city, current_reading)
    
    def _detect_events_internal(self, city: str, current_reading: Dict[str, Any]) -> List[Dict]:
        """
        Internal: detect events (shared by detect_events and detect).
        
        Returns:
            List of event dicts
        """
        events = []
        
        # Get recent readings for context
        recent_readings = self.db.get_readings_for_city(city, limit=10)
        
        # Run detection checks
        events.extend(self._check_extreme_temperature(city, current_reading))
        events.extend(self._check_rapid_temperature_change(city, current_reading, recent_readings))
        events.extend(self._check_precipitation(city, current_reading))
        events.extend(self._check_wind(city, current_reading))
        events.extend(self._check_weather_code_transition(city, current_reading, recent_readings))
        
        return events
    
    def _check_extreme_temperature(self, city: str, reading: Dict[str, Any]) -> List[Dict]:
        """Detect extreme temperatures relative to city's climate profile."""
        events = []
        temp = reading.get("temperature_2m")
        
        if temp is None:
            return events
        
        thresholds = CITY_TEMP_THRESHOLDS.get(city, {})
        extreme_high = thresholds.get("extreme_high", 30)
        extreme_low = thresholds.get("extreme_low", -20)
        
        if temp > extreme_high:
            events.append({
                "type": "extreme_heat",
                "severity": "high" if temp > extreme_high + 5 else "medium",
                "description": f"Extreme heat: {temp}°C (threshold: {extreme_high}°C for {city})",
                "data": {"temperature": temp, "threshold": extreme_high}
            })
        elif temp < extreme_low:
            events.append({
                "type": "extreme_cold",
                "severity": "high" if temp < extreme_low - 5 else "medium",
                "description": f"Extreme cold: {temp}°C (threshold: {extreme_low}°C for {city})",
                "data": {"temperature": temp, "threshold": extreme_low}
            })
        
        return events
    
    def _check_rapid_temperature_change(self, city: str, current: Dict[str, Any], 
                                       recent: List[Dict[str, Any]]) -> List[Dict]:
        """Detect rapid temperature changes (signals real weather systems)."""
        events = []
        
        current_temp = current.get("temperature_2m")
        if current_temp is None or not recent:
            return events
        
        # Find most recent previous reading (within last 1-2 hours)
        prev_reading = None
        for reading in recent:
            if reading.get("timestamp") < current.get("timestamp", 0):
                prev_reading = reading
                break
        
        if not prev_reading:
            return events
        
        prev_temp = prev_reading.get("temperature_2m")
        if prev_temp is None:
            return events
        
        temp_change = abs(current_temp - prev_temp)
        threshold = CITY_TEMP_THRESHOLDS.get(city, {}).get("unusual_change", 8)
        
        if temp_change > threshold:
            direction = "warming" if current_temp > prev_temp else "cooling"
            events.append({
                "type": "rapid_temperature_change",
                "severity": "high" if temp_change > threshold + 3 else "medium",
                "description": f"Rapid {direction}: {temp_change:.1f}°C change in ~1 hour",
                "data": {
                    "change": round(temp_change, 1),
                    "from": round(prev_temp, 1),
                    "to": round(current_temp, 1)
                }
            })
        
        return events
    
    def _check_precipitation(self, city: str, reading: Dict[str, Any]) -> List[Dict]:
        """Detect significant precipitation."""
        events = []
        
        precip = reading.get("precipitation")
        if precip is None or precip == 0:
            return events
        
        if precip > PRECIPITATION_THRESHOLD:
            events.append({
                "type": "heavy_precipitation",
                "severity": "medium" if precip < 20 else "high",
                "description": f"Heavy precipitation: {precip}mm/hour",
                "data": {"precipitation_mm": precip}
            })
        elif precip > 2:
            events.append({
                "type": "precipitation_event",
                "severity": "low",
                "description": f"Precipitation: {precip}mm/hour",
                "data": {"precipitation_mm": precip}
            })
        
        return events
    
    def _check_wind(self, city: str, reading: Dict[str, Any]) -> List[Dict]:
        """Detect significant wind conditions."""
        events = []
        
        wind = reading.get("wind_speed_10m")
        if wind is None:
            return events
        
        warning_threshold = WIND_WARNING_THRESHOLD.get(city, 50)
        alert_threshold = WIND_THRESHOLD.get(city, 40)
        
        if wind > warning_threshold:
            events.append({
                "type": "extreme_wind",
                "severity": "high",
                "description": f"Extreme wind: {wind}km/h (warning threshold: {warning_threshold}km/h)",
                "data": {"wind_speed_kmh": wind, "threshold": warning_threshold}
            })
        elif wind > alert_threshold:
            events.append({
                "type": "high_wind",
                "severity": "medium",
                "description": f"High wind: {wind}km/h",
                "data": {"wind_speed_kmh": wind}
            })
        
        return events
    
    def _check_weather_code_transition(self, city: str, current: Dict[str, Any],
                                       recent: List[Dict[str, Any]]) -> List[Dict]:
        """
        Detect significant weather code transitions (e.g., clear to precipitation).
        WMO codes indicate weather type.
        """
        events = []
        
        current_code = current.get("weather_code")
        if current_code is None or not recent:
            return events
        
        prev_reading = None
        for reading in recent:
            if reading.get("timestamp") < current.get("timestamp", 0):
                prev_reading = reading
                break
        
        if not prev_reading:
            return events
        
        prev_code = prev_reading.get("weather_code")
        if prev_code is None or prev_code == current_code:
            return events
        
        # Only flagging transitions that represent a meaningful condition change.
        # clear->overcast is not interesting. clear->precipitation is.
        # There are WMO codes this doesn't catch (e.g. fog->snow) — known gap.
        
        # Classify codes into categories
        def get_weather_category(code):
            if code == 0:
                return "clear"
            elif code in [1, 2]:
                return "partly_cloudy"
            elif code == 3:
                return "overcast"
            elif code in [45, 48]:
                return "fog"
            elif code in [51, 53, 55, 61, 63, 65, 80, 81, 82]:
                return "precipitation"
            elif code in [71, 73, 75, 77, 85, 86]:
                return "snow"
            elif code in [80, 81, 82]:
                return "showers"
            elif code in [95, 96, 99]:
                return "thunderstorm"
            else:
                return "other"
        
        prev_cat = get_weather_category(prev_code)
        curr_cat = get_weather_category(current_code)
        
        # Significant transitions
        transitions = {
            ("clear", "precipitation"): ("high", "Conditions changed: clear to precipitation"),
            ("clear", "thunderstorm"): ("high", "Conditions changed: clear to thunderstorm"),
            ("precipitation", "clear"): ("medium", "Precipitation ended"),
            ("snow", "precipitation"): ("medium", "Precipitation type changed: snow to rain"),
            ("precipitation", "snow"): ("medium", "Precipitation type changed: rain to snow"),
        }
        
        key = (prev_cat, curr_cat)
        if key in transitions:
            severity, desc = transitions[key]
            events.append({
                "type": "weather_transition",
                "severity": severity,
                "description": desc,
                "data": {"from_code": prev_code, "to_code": current_code}
            })
        
        return events
