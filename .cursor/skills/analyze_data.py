#!/usr/bin/env python3
"""
Data Analysis Skill

I wrote this because I kept opening SQLite manually to spot-check the database.
It got tedious. Now I can run quick queries from the command line to see:

- What was the highest temperature recorded in Vancouver?
- How many high-wind events occurred in each city?
- What's the trend in temperature over the last 24 hours?
- Which city had the most events?

This helps me debug detection logic and validate that thresholds are reasonable.

Usage examples:
  python3 .cursor/skills/analyze_data.py --query temperature_summary --city Ottawa
  python3 .cursor/skills/analyze_data.py --query event_summary --hours 24
  python3 .cursor/skills/analyze_data.py --query city_comparison

Supported queries:
  - temperature_summary: Min/max/avg per city over time window
  - event_summary: Event distribution by type, severity, city
  - recent_extremes: The most recent high/medium severity events
  - city_comparison: Side-by-side weather and event comparison
"""
import sys
import argparse
import json
from pathlib import Path
from datetime import datetime, timedelta

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from database import Database


def analyze_temperature_summary(db, city=None, hours=24):
    """Analyze min/max/avg temperatures."""
    cutoff_time = int((datetime.now() - timedelta(hours=hours)).timestamp())
    
    if city:
        readings = db.get_readings_for_city(city, limit=1000)
        readings = [r for r in readings if r.get("timestamp", 0) >= cutoff_time]
        cities = [city]
    else:
        all_readings = db.get_readings(limit=10000)
        readings = [r for r in all_readings if r.get("timestamp", 0) >= cutoff_time]
        cities = set(r["city"] for r in readings)
    
    results = {}
    for c in sorted(cities):
        city_readings = [r for r in readings if r["city"] == c]
        
        if not city_readings:
            results[c] = {"status": "no_data"}
            continue
        
        temps = [r["temperature_2m"] for r in city_readings if r.get("temperature_2m") is not None]
        
        if not temps:
            results[c] = {"status": "no_temperature_data"}
            continue
        
        results[c] = {
            "count": len(temps),
            "min": round(min(temps), 1),
            "max": round(max(temps), 1),
            "avg": round(sum(temps) / len(temps), 1),
            "range": round(max(temps) - min(temps), 1),
            "hours": hours
        }
    
    return results


def analyze_event_summary(db, city=None, hours=24):
    """Analyze event distribution by type and severity."""
    cutoff_time = int((datetime.now() - timedelta(hours=hours)).timestamp())
    
    if city:
        events = db.get_events(city=city, limit=10000)
    else:
        events = db.get_events(limit=10000)
    
    # Filter by time
    recent_events = []
    for e in events:
        detected_at_str = e.get("detected_at", "")
        if detected_at_str:
            try:
                detected_time = datetime.fromisoformat(detected_at_str).timestamp()
                if detected_time > cutoff_time:
                    recent_events.append(e)
            except ValueError:
                # Skip events with invalid timestamp format
                pass
    
    by_type = {}
    by_severity = {}
    by_city = {}
    
    for event in recent_events:
        # By type
        evt_type = event.get("event_type", "unknown")
        by_type[evt_type] = by_type.get(evt_type, 0) + 1
        
        # By severity
        severity = event.get("severity", "unknown")
        by_severity[severity] = by_severity.get(severity, 0) + 1
        
        # By city
        evt_city = event.get("city", "unknown")
        by_city[evt_city] = by_city.get(evt_city, 0) + 1
    
    return {
        "total_events": len(recent_events),
        "by_type": dict(sorted(by_type.items(), key=lambda x: x[1], reverse=True)),
        "by_severity": dict(sorted(by_severity.items(), key=lambda x: x[1], reverse=True)),
        "by_city": dict(sorted(by_city.items(), key=lambda x: x[1], reverse=True)),
        "hours": hours
    }


def analyze_recent_extremes(db, limit=10):
    """Get recent high-severity events."""
    events = db.get_events(limit=limit*2)
    
    high_severity = [e for e in events if e.get("severity") in ["high", "medium"]][:limit]
    
    result = {
        "extreme_events": []
    }
    
    for event in high_severity:
        result["extreme_events"].append({
            "city": event.get("city"),
            "type": event.get("event_type"),
            "severity": event.get("severity"),
            "description": event.get("description"),
            "detected": event.get("detected_at"),
            "data": event.get("data")
        })
    
    return result


def analyze_city_comparison(db, hours=24):
    """Compare conditions across all cities."""
    temps = analyze_temperature_summary(db, hours=hours)
    events = analyze_event_summary(db, hours=hours)
    
    comparison = {
        "timestamp": datetime.now().isoformat(),
        "period_hours": hours,
        "cities": {}
    }
    
    for city in ["Ottawa", "Toronto", "Vancouver"]:
        readings = db.get_readings_for_city(city, limit=1000)
        cutoff_time = int((datetime.now() - timedelta(hours=hours)).timestamp())
        readings = [r for r in readings if r.get("timestamp", 0) >= cutoff_time]
        
        if not readings:
            continue
        
        winds = [r["wind_speed_10m"] for r in readings if r.get("wind_speed_10m") is not None]
        precips = [r["precipitation"] for r in readings if r.get("precipitation", 0) > 0]
        
        city_events = [e for e in db.get_events(city=city, limit=1000) 
                      if e.get("severity") in ["high", "medium"]]
        
        comparison["cities"][city] = {
            "temp": temps.get(city, {}),
            "wind_avg": round(sum(winds) / len(winds), 1) if winds else None,
            "total_precipitation": round(sum(precips), 1) if precips else 0,
            "high_severity_events": len(city_events)
        }
    
    return comparison


def main():
    parser = argparse.ArgumentParser(
        description="WatchAgent data analysis tool"
    )
    parser.add_argument(
        "--query",
        default="temperature_summary",
        choices=["temperature_summary", "event_summary", "recent_extremes", "city_comparison"],
        help="Type of analysis to run"
    )
    parser.add_argument(
        "--city",
        choices=["Ottawa", "Toronto", "Vancouver"],
        help="Filter by city (if applicable)"
    )
    parser.add_argument(
        "--hours",
        type=int,
        default=24,
        help="Hours of data to analyze (default: 24)"
    )
    parser.add_argument(
        "--db",
        default="/data/watchagent.db",
        help="Path to database (default: /data/watchagent.db)"
    )
    
    args = parser.parse_args()
    
    # Try to connect to database
    try:
        db = Database(db_path=args.db)
    except Exception as e:
        print(json.dumps({
            "error": f"Failed to connect to database: {e}",
            "db_path": args.db
        }, indent=2))
        sys.exit(1)
    
    # Run the requested analysis
    try:
        if args.query == "temperature_summary":
            result = analyze_temperature_summary(db, city=args.city, hours=args.hours)
        elif args.query == "event_summary":
            result = analyze_event_summary(db, city=args.city, hours=args.hours)
        elif args.query == "recent_extremes":
            result = analyze_recent_extremes(db, limit=10)
        elif args.query == "city_comparison":
            result = analyze_city_comparison(db, hours=args.hours)
        else:
            result = {"error": "Unknown query type"}
        
        # Add query metadata
        result["query"] = args.query
        result["timestamp"] = datetime.now().isoformat()
        
        print(json.dumps(result, indent=2))
    
    except Exception as e:
        print(json.dumps({
            "error": str(e),
            "query": args.query
        }, indent=2))
        sys.exit(1)


if __name__ == "__main__":
    main()
