---
name: Data Structure and Storage
description: What columns go in the database, how readings and events are shaped, what the API returns
---

# Data Structure and Storage

## The Deduplication Strategy

(city, timestamp) is the natural dedup key. I enforce it TWO ways:
1. UNIQUE(city, timestamp) constraint in the database
2. last_timestamps dict in the poller to pre-filter

Redundant? Yes, intentionally. I don't want to rely only on catching sqlite3.IntegrityError — that feels fragile. If the poller's dict ever gets out of sync with reality, the database constraint still catches it.

## Timestamp Conversion (CRITICAL BUG FIX)

Open-Meteo returns time as ISO string "2026-05-29T14:00", NOT a Unix timestamp. 

I stored it as a string the first time. UNIQUE constraint still worked (same string = duplicate). BUT queries with `ORDER BY timestamp DESC` did alphabetical sort, not chronological. So newest readings appeared as oldest. Total disaster.

Fix: Always convert immediately:
```python
int(datetime.fromisoformat(iso_time).timestamp())
```

Store as INTEGER in the database, never as string. This is in poller.py line 67.

Readings table has: city, timestamp (int), temperature_2m, apparent_temperature, precipitation, wind_speed_10m, weather_code. All the temp/precip/wind fields can be NULL if the API doesn't return them. The Unique constraint is just on (city, timestamp).

Events table needs: city, event_type, timestamp (when the weather thing happened, not when we detected it), severity (low/medium/high), description, and a data JSON blob for structured context.

## Event Descriptions

Description matters. Never store generic "Extreme heat detected". Instead:
- "34.2°C exceeds Ottawa threshold of 32°C"
- "10.5°C warming in 1 hour from previous reading"

The description needs BOTH the actual value AND the threshold so when someone reviews events later, they understand exactly what happened and why the detector fired.

## API Response Format

All responses use this consistent wrapper:
```json
{
  "readings": [...],
  "count": 50,
  "filter": {"city": "Ottawa", "limit": 50}
}
```

Never return a bare list. I decided this upfront because if I ever add pagination or sorting, the response structure won't need to change — just add fields to the wrapper.

Timestamp fields: readings.stored_at is TIMESTAMP DEFAULT CURRENT_TIMESTAMP (when we inserted it). events.detected_at is the same (when we detected the event). readings.timestamp and events.timestamp are the Unix integers from Open-Meteo (when the weather thing actually happened).
