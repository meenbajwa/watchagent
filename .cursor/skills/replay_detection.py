#!/usr/bin/env python3
"""
Replay Detection Skill

I built this to answer: "If I change the extreme_heat threshold from 32°C to 31°C,
what events would have fired differently on last week's data?"

You give it a city and date range, it replays all those readings through the
detector and shows you EXACTLY which events fired for each one. Really useful
for validating threshold changes before deploying them.

Usage:
    python3 .cursor/skills/replay_detection.py --city Ottawa --limit 50
    python3 .cursor/skills/replay_detection.py --city Vancouver --days 7
    python3 .cursor/skills/replay_detection.py --db /data/watchagent.db --limit 100

Outputs:
- Each reading with its conditions (temp, wind, precipitation)
- Which events fired for that reading
- Summary of event counts

This is how I validated that my city-specific thresholds actually work.
"""
import sys
import json
import logging
from pathlib import Path
from datetime import datetime, timedelta
from argparse import ArgumentParser

logger = logging.getLogger(__name__)

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from database import Database
from detector import EventDetector


def format_reading(reading):
    """Format a reading dict for display."""
    return {
        "city": reading["city"],
        "timestamp": reading["timestamp"],
        "temp": reading["temperature_2m"],
        "apparent_temp": reading["apparent_temperature"],
        "precipitation": reading["precipitation"],
        "wind_speed": reading["wind_speed_10m"],
        "weather_code": reading["weather_code"],
    }


def replay_readings(db_path: str, city: str = None, limit: int = 20, days: int = None):
    """
    Replay readings and show what events would fire.
    
    Args:
        db_path: Path to SQLite database
        city: Filter by city (optional)
        limit: Maximum readings to process
        days: Only include readings from last N days (optional)
    
    Returns:
        Dict with results and event summary
    """
    db = Database(db_path)
    detector = EventDetector(db)
    
    # Get readings from database
    if city:
        readings = db.get_readings_for_city(city, limit=limit)
    else:
        readings = db.get_readings(limit=limit)
    
    if not readings:
        return {
            "status": "no readings found",
            "query": {"city": city, "limit": limit, "days": days},
        }
    
    # Filter by days if specified
    if days:
        from time import time
        cutoff = int(time()) - (days * 86400)
        readings = [r for r in readings if r.get("timestamp", 0) >= cutoff]
    
    if not readings:
        return {
            "status": "no readings found for specified time range",
            "query": {"city": city, "limit": limit, "days": days},
        }
    
    # Reverse to chronological order for replay
    readings = list(reversed(readings))
    
    replay_results = []
    event_counts = {}
    
    for reading in readings:
        city = reading.get("city")
        if not city:
            logger.warning("Reading has no city, skipping")
            continue
        
        events = detector.detect(city, reading)
        
        for event in events:
            event_type = event.get("type")
            event_counts[event_type] = event_counts.get(event_type, 0) + 1
        
        replay_results.append({
            "reading": format_reading(reading),
            "events_fired": [
                {
                    "type": e.get("type"),
                    "severity": e.get("severity"),
                    "description": e.get("description"),
                    "data": e.get("data"),
                }
                for e in events
            ],
        })
    
    return {
        "status": "ok",
        "query": {"city": city, "limit": limit, "days": days},
        "readings_replayed": len(readings),
        "replay": replay_results,
        "event_summary": event_counts,
        "total_events": sum(event_counts.values()),
    }


if __name__ == "__main__":
    parser = ArgumentParser(description="Replay readings through detection logic")
    parser.add_argument("--city", type=str, help="Filter by city (Ottawa, Toronto, Vancouver)")
    parser.add_argument("--limit", type=int, default=20, help="Max readings to process")
    parser.add_argument("--days", type=int, help="Only last N days of readings")
    parser.add_argument("--db", type=str, default="/data/watchagent.db", 
                       help="Path to database (default: /data/watchagent.db)")
    
    args = parser.parse_args()
    
    try:
        result = replay_readings(
            db_path=args.db,
            city=args.city,
            limit=args.limit,
            days=args.days,
        )
        print(json.dumps(result, indent=2))
    except Exception as e:
        print(json.dumps({
            "status": "error",
            "error": str(e),
        }), file=sys.stderr)
        sys.exit(1)
