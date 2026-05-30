"""
Database layer for readings and events.
Handles all persistence to SQLite with proper transaction handling.
"""
import sqlite3
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any

logger = logging.getLogger(__name__)


class Database:
    """Manages SQLite database for readings and events."""
    
    def __init__(self, db_path: str = "watchagent.db"):
        self.db_path = db_path
        self.init_schema()
    
    def init_schema(self):
        # Using SQLite here rather than Postgres because this is a single-process
        # service and SQLite is zero-config. The UNIQUE constraint on (city, timestamp)
        # is the real dedup guarantee — the poller's last_timestamps dict is just
        # an optimization to avoid hitting the DB unnecessarily.
        """Create tables if they don't exist."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS readings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    city TEXT NOT NULL,
                    timestamp INTEGER NOT NULL,
                    temperature_2m REAL,
                    apparent_temperature REAL,
                    precipitation REAL,
                    wind_speed_10m REAL,
                    weather_code INTEGER,
                    stored_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(city, timestamp)
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    city TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    timestamp INTEGER NOT NULL,
                    severity TEXT,
                    description TEXT,
                    data JSON,
                    detected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            # Index on (city, timestamp DESC) makes the "get last N readings for city"
            # query fast. Without it, every detection check was doing a full table scan.
            conn.execute("CREATE INDEX IF NOT EXISTS idx_readings_city_time ON readings(city, timestamp DESC)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_events_city_time ON events(city, detected_at DESC)")
            conn.commit()
            logger.info(f"Database initialized at {self.db_path}")
    
    def store_reading(self, city: str, data: Dict[str, Any]) -> bool:
        """
        Store a weather reading. Returns True if stored, False if duplicate.
        
        Args:
            city: City name
            data: Weather data with keys: timestamp, temperature_2m, apparent_temperature, etc.
        
        Returns:
            True if new reading stored, False if duplicate (same timestamp for city)
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("""
                    INSERT INTO readings 
                    (city, timestamp, temperature_2m, apparent_temperature, 
                     precipitation, wind_speed_10m, weather_code)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (
                    city,
                    data['timestamp'],
                    data.get('temperature_2m'),
                    data.get('apparent_temperature'),
                    data.get('precipitation'),
                    data.get('wind_speed_10m'),
                    data.get('weather_code')
                ))
                conn.commit()
                return True
        except sqlite3.IntegrityError:
            # Duplicate timestamp for this city
            return False
        except Exception as e:
            logger.error(f"Failed to store reading for {city}: {e}")
            return False
    
    def store_event(self, city: str, event_type: str, timestamp: int, 
                   severity: str, description: str, data: Dict[str, Any] = None):
        """
        Store a detected event.
        
        Args:
            city: City where event occurred
            event_type: Type of event (e.g., 'extreme_temp', 'rapid_change')
            timestamp: Unix timestamp when event occurred
            severity: 'low', 'medium', 'high'
            description: Human-readable description
            data: Optional structured data about the event
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("""
                    INSERT INTO events 
                    (city, event_type, timestamp, severity, description, data)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (
                    city,
                    event_type,
                    timestamp,
                    severity,
                    description,
                    json.dumps(data) if data else None
                ))
                conn.commit()
                logger.info(f"Event stored: {city} - {event_type} ({severity})")
        except Exception as e:
            logger.error(f"Failed to store event: {e}")
    
    def get_readings(self, city: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
        """Get most recent readings, optionally filtered by city."""
        query = "SELECT * FROM readings"
        params = []
        
        if city:
            query += " WHERE city = ?"
            params.append(city)
        
        query += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)
        
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(query, params).fetchall()
            return [dict(row) for row in rows]
    
    def get_events(self, city: Optional[str] = None, limit: int = 50) -> List[Dict[str, Any]]:
        """Get most recent events, optionally filtered by city."""
        query = "SELECT * FROM events"
        params = []
        
        if city:
            query += " WHERE city = ?"
            params.append(city)
        
        query += " ORDER BY detected_at DESC LIMIT ?"
        params.append(limit)
        
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(query, params).fetchall()
            result = []
            for row in rows:
                d = dict(row)
                if d.get('data'):
                    d['data'] = json.loads(d['data'])
                result.append(d)
            return result
    
    def get_readings_for_city(self, city: str, limit: int = 100) -> List[Dict[str, Any]]:
        """Get recent readings for a specific city for analysis."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM readings WHERE city = ? ORDER BY timestamp DESC LIMIT ?",
                (city, limit)
            ).fetchall()
            return [dict(row) for row in rows]
    
    def count_readings(self) -> int:
        """Total number of readings stored."""
        with sqlite3.connect(self.db_path) as conn:
            result = conn.execute("SELECT COUNT(*) FROM readings").fetchone()
            return result[0] if result else 0
    
    def count_events(self) -> int:
        """Total number of events stored."""
        with sqlite3.connect(self.db_path) as conn:
            result = conn.execute("SELECT COUNT(*) FROM events").fetchone()
            return result[0] if result else 0
