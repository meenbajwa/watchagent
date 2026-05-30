"""
Unit tests for WatchAgent.

Tests cover:
- Deduplication (same timestamp for a city is not stored twice)
- Event detection logic (given controlled inputs, correct events fire)
- API endpoints (health, readings, events return correct structure)
"""
import pytest
import tempfile
import os
import json
from pathlib import Path

# Add src to path
import sys
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from database import Database
from detector import EventDetector
from poller import WeatherPoller


@pytest.fixture
def temp_db():
    """Create a temporary database for testing."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    
    db = Database(db_path=db_path)
    yield db
    
    # Cleanup
    if os.path.exists(db_path):
        os.unlink(db_path)


@pytest.fixture
def detector(temp_db):
    """Create an event detector."""
    return EventDetector(temp_db)


class TestDeduplication:
    """Tests for deduplication logic."""
    
    def test_duplicate_reading_not_stored(self, temp_db):
        """Same timestamp for city should not create duplicate rows."""
        reading = {
            "timestamp": 1234567890,
            "temperature_2m": 20.0,
            "apparent_temperature": 19.0,
            "precipitation": 0,
            "wind_speed_10m": 10,
            "weather_code": 0
        }
        
        # Store first reading
        result1 = temp_db.store_reading("Ottawa", reading)
        assert result1 is True, "First reading should be stored"
        
        # Try to store identical reading
        result2 = temp_db.store_reading("Ottawa", reading)
        assert result2 is False, "Duplicate reading should not be stored"
        
        # Verify only one row in database
        readings = temp_db.get_readings("Ottawa")
        assert len(readings) == 1, "Should have exactly one reading"
    
    def test_different_timestamp_stored(self, temp_db):
        """Different timestamp for same city should create new row."""
        reading1 = {
            "timestamp": 1234567890,
            "temperature_2m": 20.0,
            "apparent_temperature": 19.0,
            "precipitation": 0,
            "wind_speed_10m": 10,
            "weather_code": 0
        }
        
        reading2 = {
            "timestamp": 1234567900,  # Different timestamp
            "temperature_2m": 21.0,
            "apparent_temperature": 20.0,
            "precipitation": 0,
            "wind_speed_10m": 10,
            "weather_code": 0
        }
        
        temp_db.store_reading("Ottawa", reading1)
        temp_db.store_reading("Ottawa", reading2)
        
        readings = temp_db.get_readings("Ottawa")
        assert len(readings) == 2, "Should have two distinct readings"


class TestEventDetection:
    """
    Tests for event detection logic.
    
    These tests define the contract for detection logic.
    
    Each test constructs a specific reading (or sequence) and asserts
    exactly which events fire. If a threshold changes in detector.py,
    these tests should catch it.
    
    The 'no event for normal conditions' test is intentionally boring —
    it's checking that the system isn't noisy, which matters as much
    as catching real events.
    """
    
    def test_extreme_heat_detected(self, temp_db, detector):
        """Extreme heat for Ottawa should trigger event."""
        reading = {
            "timestamp": 1234567890,
            "temperature_2m": 38.0,  # 38°C is extreme_high (32°C) + 6°C = triggers "high" severity
            "apparent_temperature": 40.0,
            "precipitation": 0,
            "wind_speed_10m": 5,
            "weather_code": 0
        }
        
        detector.detect_events("Ottawa", reading)
        
        events = temp_db.get_events("Ottawa")
        assert len(events) > 0, "Should detect extreme heat event"
        
        heat_events = [e for e in events if e["event_type"] == "extreme_heat"]
        assert len(heat_events) > 0, "Should have extreme_heat event type"
        assert heat_events[0]["severity"] == "high", "38°C should be high severity for Ottawa"
    
    def test_extreme_cold_detected(self, temp_db, detector):
        """Extreme cold should trigger event."""
        reading = {
            "timestamp": 1234567890,
            "temperature_2m": -28.0,
            "apparent_temperature": -35.0,
            "precipitation": 0,
            "wind_speed_10m": 20,
            "weather_code": 75
        }
        
        detector.detect_events("Ottawa", reading)
        
        events = temp_db.get_events("Ottawa")
        cold_events = [e for e in events if e["event_type"] == "extreme_cold"]
        assert len(cold_events) > 0, "Should detect extreme cold"
    
    def test_heavy_precipitation_detected(self, temp_db, detector):
        """Heavy precipitation should trigger event."""
        reading = {
            "timestamp": 1234567890,
            "temperature_2m": 15.0,
            "apparent_temperature": 14.0,
            "precipitation": 12.0,  # Heavy rain (> 10mm threshold)
            "wind_speed_10m": 15,
            "weather_code": 61
        }
        
        detector.detect_events("Toronto", reading)
        
        events = temp_db.get_events("Toronto")
        precip_events = [e for e in events if e["event_type"] == "heavy_precipitation"]
        assert len(precip_events) > 0, "Should detect heavy precipitation"
    
    def test_high_wind_detected(self, temp_db, detector):
        """High wind should trigger event."""
        reading = {
            "timestamp": 1234567890,
            "temperature_2m": 10.0,
            "apparent_temperature": 5.0,
            "precipitation": 0,
            "wind_speed_10m": 45.0,  # High wind
            "weather_code": 3
        }
        
        detector.detect_events("Ottawa", reading)
        
        events = temp_db.get_events("Ottawa")
        wind_events = [e for e in events if e["event_type"] in ["high_wind", "extreme_wind"]]
        assert len(wind_events) > 0, "Should detect high wind"
    
    def test_no_event_for_normal_conditions(self, temp_db, detector):
        """Normal conditions should not trigger events."""
        reading = {
            "timestamp": 1234567890,
            "temperature_2m": 15.0,
            "apparent_temperature": 14.0,
            "precipitation": 0,
            "wind_speed_10m": 8,
            "weather_code": 1
        }
        
        detector.detect_events("Toronto", reading)
        
        events = temp_db.get_events("Toronto")
        assert len(events) == 0, "Normal conditions should not trigger events"
    
    def test_rapid_temperature_change_detected(self, temp_db, detector):
        """Rapid temperature change should be detected."""
        # Two readings are needed for this check — the detector compares
        # current to previous. Storing reading1 first, then running detection
        # on reading2 simulates what happens in a real poll cycle.
        reading1 = {
            "timestamp": 1234567800,
            "temperature_2m": 5.0,
            "apparent_temperature": 3.0,
            "precipitation": 0,
            "wind_speed_10m": 5,
            "weather_code": 0
        }
        
        # Store first reading
        temp_db.store_reading("Ottawa", reading1)
        
        # Second reading with rapid change
        reading2 = {
            "timestamp": 1234567890,
            "temperature_2m": 15.0,  # 10°C change
            "apparent_temperature": 14.0,
            "precipitation": 0,
            "wind_speed_10m": 5,
            "weather_code": 0
        }
        
        detector.detect_events("Ottawa", reading2)
        
        events = temp_db.get_events("Ottawa")
        temp_change_events = [e for e in events if e["event_type"] == "rapid_temperature_change"]
        assert len(temp_change_events) > 0, "Should detect rapid temperature change"
    
    def test_vancouver_threshold_lower_than_ottawa(self, temp_db, detector):
        """
        Vancouver's extreme_heat threshold should be lower than Ottawa's.
        
        This test proves city-aware thresholds work correctly:
        - Vancouver at 27°C should fire extreme_heat
        - Ottawa at 27°C should NOT fire extreme_heat (threshold is 32°C)
        """
        # Vancouver reading at 27°C
        vancouver_reading = {
            "timestamp": 1234567890,
            "temperature_2m": 27.0,
            "apparent_temperature": 27.0,
            "precipitation": 0,
            "wind_speed_10m": 5,
            "weather_code": 0
        }
        
        # Ottawa reading at 27°C (same temperature, different city)
        ottawa_reading = {
            "timestamp": 1234567890,
            "temperature_2m": 27.0,
            "apparent_temperature": 27.0,
            "precipitation": 0,
            "wind_speed_10m": 5,
            "weather_code": 0
        }
        
        # Store readings so detector can access history if needed
        temp_db.store_reading("Vancouver", vancouver_reading)
        temp_db.store_reading("Ottawa", ottawa_reading)
        
        # Detect events for both cities
        detector.detect_events("Vancouver", vancouver_reading)
        detector.detect_events("Ottawa", ottawa_reading)
        
        # Get events from database
        vancouver_events = temp_db.get_events("Vancouver")
        ottawa_events = temp_db.get_events("Ottawa")
        
        # Vancouver should have extreme_heat at 27°C
        vancouver_heat_events = [e for e in vancouver_events if e.get("event_type") == "extreme_heat"]
        assert len(vancouver_heat_events) > 0, "Vancouver should detect extreme_heat at 27°C"
        
        # Ottawa should NOT have extreme_heat at 27°C (threshold is 32°C)
        ottawa_heat_events = [e for e in ottawa_events if e.get("event_type") == "extreme_heat"]
        assert len(ottawa_heat_events) == 0, "Ottawa should NOT detect extreme_heat at 27°C"


class TestAPI:
    """Tests for API endpoints."""
    
    def test_health_endpoint(self, temp_db):
        """Health endpoint should return correct structure."""
        from api import app, init_api
        from fastapi.testclient import TestClient
        
        init_api(temp_db)
        client = TestClient(app)
        
        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        
        assert "status" in data
        assert data["status"] == "ok"
        assert "readings_stored" in data
        assert "events_stored" in data
        assert isinstance(data["readings_stored"], int)
        assert isinstance(data["events_stored"], int)
    
    def test_readings_endpoint(self, temp_db):
        """Readings endpoint should return correct structure."""
        from api import app, init_api
        from fastapi.testclient import TestClient
        
        # Seed some readings
        reading = {
            "timestamp": 1234567890,
            "temperature_2m": 20.0,
            "apparent_temperature": 19.0,
            "precipitation": 0,
            "wind_speed_10m": 10,
            "weather_code": 0
        }
        temp_db.store_reading("Ottawa", reading)
        temp_db.store_reading("Toronto", reading)
        
        init_api(temp_db)
        client = TestClient(app)
        
        # Test without filter
        response = client.get("/readings")
        assert response.status_code == 200
        data = response.json()
        assert "readings" in data
        assert "count" in data
        assert len(data["readings"]) >= 2
        
        # Test with city filter
        response = client.get("/readings?city=Ottawa")
        assert response.status_code == 200
        data = response.json()
        filtered = [r for r in data["readings"] if r["city"] == "Ottawa"]
        assert len(filtered) > 0
    
    def test_events_endpoint(self, temp_db, detector):
        """Events endpoint should return correct structure."""
        from api import app, init_api
        from fastapi.testclient import TestClient
        
        # Create and store an event
        reading = {
            "timestamp": 1234567890,
            "temperature_2m": 35.0,
            "apparent_temperature": 38.0,
            "precipitation": 0,
            "wind_speed_10m": 5,
            "weather_code": 0
        }
        detector.detect_events("Ottawa", reading)
        
        init_api(temp_db)
        client = TestClient(app)
        
        # Test without filter
        response = client.get("/events")
        assert response.status_code == 200
        data = response.json()
        assert "events" in data
        assert "count" in data
        assert len(data["events"]) > 0
        
        # Verify event structure
        event = data["events"][0]
        assert "event_type" in event
        assert "severity" in event
        assert "description" in event
