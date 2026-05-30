---
name: Error Handling and Retry Logic
description: How failures get handled without crashing the poller or API
---

# Error Handling and Retry Logic

## Network Failures (Open-Meteo API)

When fetch_city_weather() fails, log at WARNING with city, http_status, and attempt number. Return None. Do NOT raise exceptions. 

I learned early that raising kills the entire poll_once() call — if Ottawa times out, Toronto and Vancouver never run that cycle. Bad. Now I catch failures per-city and keep going.

Exponential backoff on retries: 2^attempt seconds. 
- Attempt 1 fails → wait 2s, retry
- Attempt 2 fails → wait 4s, retry  
- Attempt 3 fails → log ERROR, give up, move to next city

The poller should never stop because one city's API is flaky. This keeps us honest about SLA expectations.

## Database Failures

**sqlite3.IntegrityError when inserting** = almost always a duplicate (same city + timestamp) = EXPECTED behavior. Return False, don't log as error.

I tried raising on duplicate first, but that meant catching and handling the exception everywhere. Returning False is cleaner. Caller can check the boolean and decide what to do.

Any other database exception = real failure. Log ERROR with full context and return False. Both API and poller degrade gracefully.

## Detection Logic Resilience

In detector.py I use .get() EVERYWHERE instead of direct key access. Open-Meteo sometimes returns `null` for optional fields (apparent_temperature, weather_code, etc.) and I had a bug where one missed `.get()` caused a KeyError that silently crashed detection for an entire reading. Never again. Now I check every single field with `.get()`.

## Logging Strategy

I use these levels consistently:
- **DEBUG**: Duplicate skipped, a check was skipped due to missing field, low-noise stuff
- **INFO**: Reading stored successfully, event detected and stored
- **WARNING**: API fetch attempt failed, will retry
- **ERROR**: Gave up after max retries, database write completely failed

The API returns 500 with "Database not initialized" if db isn't ready. That's better than crashing or silently failing.
