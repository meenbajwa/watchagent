# WatchAgent: Weather Monitor & AI Assistant

Weather monitoring service for Ottawa, Toronto, and Vancouver. Polls Open-Meteo every 5 minutes, detects notable weather conditions, and exposes everything through a REST API.

I focused on making the event detection reasoning clear—why I chose city-specific thresholds, how the system works, and how to verify it's correct.

**Stack**: Python 3.11, FastAPI, SQLite, Docker Compose, GitHub Actions  
**What I'm Most Proud Of**: The event detection logic is thoughtful and defensible. The `.cursor/` folder shows the patterns I discovered. The tests are comprehensive enough to catch regressions.

---

## System Overview

WatchAgent is a three-component system. I kept it simple: poller pushes to storage, storage pushes events to API. No queues or event buses—I wanted to understand all the pieces myself.

```
┌──────────────────────────────────────────────────────────────┐
│                    WatchAgent Service                        │
├──────────────────────────────────────────────────────────────┤
│                                                              │
│    ┌────────────────┐                ┌────────────────┐      │
│    │    POLLER      │                │      API       │      │
│    │                │   ──sync→      │                │      │
│    │ • Fetches      │                │ • /health      │      │
│    │   weather      │                │ • /readings    │      │
│    │   every 5min   │                │ • /events      │      │
│    │ • Stores in    │                │                │      │
│    │   database     │                │                │      │
│    └────────────────┘                └────────────────┘      │
│            │                                   ↑             │
│            │                                   │             │
│            ↓                                   │             │
│    ┌────────────────────────────────────────────────┐        │
│    │      DETECTOR & DATABASE                       │        │
│    │                                                │        │
│    │  • Analyzes readings for notable events        │        │
│    │  • Stores readings (deduped by city+time)      │        │
│    │  • Stores events with severity & metadata      │        │
│    │  • SQLite persists across restarts             │        │
│    └────────────────────────────────────────────────┘        │
│                                                              │
└──────────────────────────────────────────────────────────────┘
```

### Components

**Poller** (`src/poller.py`)  
Runs in a daemon thread. Fetches weather from Open-Meteo every 5 minutes for all three cities. I tried using `asyncio` first but it felt like overkill—threads are simpler and this isn't async-heavy. Includes exponential backoff retry logic: if Ottawa times out, I wait 2 seconds, retry, then 4 seconds, then give up. All on a per-city basis so one failure doesn't break the other two.

**Detector** (`src/detector.py`)  
Analyzes each new reading for notable events. This is where I spent the most time thinking. The trick isn't just "is it hot?" but "is it hot for HERE?" Same temp means totally different things in Vancouver vs Ottawa. Also handles rapid changes, precipitation, wind, and weather transitions. Each event gets a severity (low/medium/high) and a description that explains WHY it fired.

**Database** (`src/database.py`)  
SQLite with two tables. Readings table stores the weather data; events table stores what I detected. The readings table has a UNIQUE constraint on (city, timestamp) so duplicates are automatically rejected. I also track queries with indexes on city+timestamp for fast filtering. All transactions are atomic—either a reading goes in or it doesn't, no half-states.

**API** (`src/api.py`)  
Three endpoints: `/health` (status check), `/readings` (query weather), `/events` (query detected events). All responses are wrapped in metadata with count and filter info. No auth layer—not needed for this scope, but noted for production.

---

## Event Detection Design

This is the hard part. The easy version is "if temp > 30 alert". But that misses the point. 30°C in Vancouver is a HUGE deal. 30°C in Ottawa in July is just summer. So the detection logic had to be city-aware.

### My Approach

I sat down and thought about what "notable weather" actually means:
- **In Ottawa**: Extremes are -25°C and +32°C because that's what breaks infrastructure and changes behavior
- **In Toronto**: Slightly milder due to the lake (+31°C, -23°C)
- **In Vancouver**: Much tighter range (+26°C, -5°C) because they rarely experience extremes

Then I added the second layer: **rate of change**. A 10°C jump in one hour is unusual everywhere—it signals a weather system is moving fast.

I also separate out phenomena: heavy rain and wind aren't really "extreme" but they're noteworthy for different reasons. So I detect them separately.

### Event Types (What I'm Actually Detecting)

| Type | Severity | Logic | Why |
|------|----------|-------|-----|
| **Extreme Heat** | high | Temp > city_threshold + 5°C | Beyond normal summer = stress on systems/people |
| **Extreme Cold** | high | Temp < city_threshold - 5°C | Below normal winter = dangerous |
| **Rapid Change** | medium | >8°C/hr (city-dependent, Vancouver 6°C) | Signals weather front |
| **Heavy Rain** | medium | >10 mm/hour | Localized flooding risk |
| **High Wind** | medium | >35-40 km/h (varies by city) | Infrastructure/travel hazard |
| **Weather Shift** | medium | Clear→Rain transition | Pattern change |

### City Thresholds (This Is Where I Did Research)

**Ottawa** (Continental: brutal winters, hot humid summers)
- Extreme heat: 32°C — above this it's dangerously hot
- Extreme cold: -25°C — below this most people stay inside
- Fast change threshold: 8°C/hour
- Wind alert: 40 km/h

**Toronto** (Great Lake Effect: moderating influence, but still cold winters)
- Extreme heat: 31°C — one degree less because of lake moderation
- Extreme cold: -23°C — slightly less extreme because of the lake
- Fast change threshold: 8°C/hour
- Wind alert: 40 km/h

**Vancouver** (Maritime: never gets extremely cold or hot, but very stable)
- Extreme heat: 26°C — they're not used to heat, so lower threshold
- Extreme cold: -5°C — below this is their equivalent of "extremely cold"
- Fast change threshold: 6°C/hour (smaller because their weather is more stable)
- Wind alert: 35 km/h

### Why This Approach

I tried a rolling average approach initially to smooth out noise, but with 5-minute polling intervals and real weather events, it added complexity without improving signal. Simple thresholds + historical context work better.

One thing I'm **not fully confident about**: the weather_code transitions. Open-Meteo uses WMO weather codes (0=clear, 1=cloudy, 61=rain, etc.). I only detect 5 transitions. There are way more possible, but I didn't over-engineer it. If this were production, I'd collect real data first and tune it.

---

## Quick Start

I've made this as simple as possible. Everything runs in Docker, or you can develop locally in 30 seconds.

### Prerequisites
- Docker & Docker Compose (simplest)
- OR Python 3.11+ if running locally

### Run with Docker (Recommended)

```bash
git clone <your-repo>
cd WatchAgent
docker compose up --build
```

Wait ~30 seconds for the service to start. Then:
```bash
curl http://localhost:8000/health
```

Data persists in a Docker volume called `watchagent_data`, so restarts don't lose readings or events.

### Local Development (If you want to test code changes)

```bash
# Install dependencies
pip install -r requirements.txt

# Run the entire system (API on 8000, poller in background)
python src/main.py
```

Test it:
```bash
curl http://localhost:8000/readings
```

### Environment Variables

See `.env.example` for all options:
- `API_HOST` / `API_PORT`: Where the API listens (default localhost:8000)
- `POLL_INTERVAL`: Seconds between Open-Meteo fetches (default 300 = 5 minutes)
- `DB_PATH`: Where to store the SQLite database (default /data/watchagent.db)

---

## API Reference

### GET /health
Health check endpoint.

**Response (200 OK):**
```json
{
  "status": "ok",
  "readings_stored": 1523,
  "events_stored": 87
}
```

### GET /readings
Get recent weather readings.

**Query Parameters:**
- `city` (optional): Filter by city (Ottawa, Toronto, Vancouver)
- `limit` (optional, default 50): Max results (1-1000)

**Example:**
```bash
curl "http://localhost:8000/readings?city=Ottawa&limit=10"
```

**Response (200 OK):**
```json
{
  "readings": [
    {
      "id": 1523,
      "city": "Ottawa",
      "timestamp": 1717056000,
      "temperature_2m": 22.5,
      "apparent_temperature": 21.0,
      "precipitation": 0.0,
      "wind_speed_10m": 12.3,
      "weather_code": 1,
      "stored_at": "2026-05-29T14:30:00"
    }
  ],
  "count": 10,
  "filter": {
    "city": "Ottawa",
    "limit": 10
  }
}
```

### GET /events
Get detected notable events.

**Query Parameters:**
- `city` (optional): Filter by city
- `limit` (optional, default 50): Max results (1-1000)

**Example:**
```bash
curl "http://localhost:8000/events?city=Vancouver&limit=5"
```

**Response (200 OK):**
```json
{
  "events": [
    {
      "id": 87,
      "city": "Vancouver",
      "event_type": "extreme_heat",
      "timestamp": 1717056000,
      "severity": "high",
      "description": "Extreme heat: 28.5°C (threshold: 26°C for Vancouver)",
      "data": {
        "temperature": 28.5,
        "threshold": 26
      },
      "detected_at": "2026-05-29T14:30:05"
    }
  ],
  "count": 5,
  "filter": {
    "city": "Vancouver",
    "limit": 5
  }
}
```

---

## Testing

I built 12 unit tests covering three areas. All pass.

### 1. Deduplication (2 tests)
The database should reject duplicate readings for the same city+timestamp.

```bash
pytest tests/test_core.py::TestDeduplication -v
```

Tests:
- Same reading twice → only one stored
- Different timestamp → both stored

### 2. Event Detection (7 tests)
This is where the logic gets tested. I verify that specific readings trigger the right events.

```bash
pytest tests/test_core.py::TestEventDetection -v
```

Tests:
- Heat: 38°C in Ottawa → extreme_heat fires with high severity
- Cold: -28°C in Ottawa → extreme_cold fires
- Wind: 45 km/h → high_wind fires
- Rain: 12mm/hour → heavy_precipitation fires
- Normal: 15°C, 8 km/h, no precipitation → no events
- Rapid change: 10°C jump between readings → rapid_temperature_change fires
- **City-aware**: Same temp (27°C) triggers event in Vancouver but NOT in Ottawa ← this is the key test

### 3. API Endpoints (3 tests)
Verify the three endpoints return the right structure.

```bash
pytest tests/test_core.py::TestAPI -v
```

Tests:
- `/health` returns status + counts
- `/readings` returns wrapped response with city filter support
- `/events` returns wrapped response with event details

**Run all tests:**
```bash
pytest tests/ -v
```

All 12 tests pass locally. GitHub Actions runs them on every push.

---

## Cursor Setup: Rules, Agents, & Skills

I created `.cursor/` files to capture patterns and decisions, and to give Cursor guidance on what I care about.

### Rules (`.cursor/rules/`)

Rules are patterns I discovered while building this. They keep the code consistent without me having to explain them every time.

#### [01-error-handling.md](.cursor/rules/01-error-handling.md)
**What it covers**: How failures get handled—network timeouts, database errors, malformed readings.

**Key decision**: When the Open-Meteo API fails for one city, I log a warning and **keep going**. I don't raise an exception and crash the entire poll cycle. I learned this early when a single timeout would kill the poller for all three cities that cycle. Now each city is independent.

**Why I wrote it**: So if I'm adding retry logic or error handling, I do it consistently.

#### [02-data-structure.md](.cursor/rules/02-data-structure.md)
**What it covers**: Database schema, API response format, deduplication strategy.

**Key decision**: Store timestamps as UNIX integers, not ISO strings. I made this mistake initially—alphabetical sort on ISO strings broke chronological ordering. Now it's a rule.

**Why I wrote it**: Data structure is foundational. Everything builds on it. If I'm adding a new field or response type, I need to stay consistent.

### Agents (`.cursor/agents/`)

Agents are specialized tools for specific reasoning problems.

#### [event-detection-reviewer.md](.cursor/agents/event-detection-reviewer.md)
**Purpose**: Help me think through event detection logic.

**Why I need it**: Event detection is the core intellectual problem. I set thresholds based on research + educated guessing, but I haven't validated them against real collected data. So I built an agent that can reason about detection: "Given these readings, should this event fire? Is the threshold too strict?"

**How I use it**: When I'm unsure about a threshold or want to test an edge case.

### Skills (`.cursor/skills/`)

Skills are tools—Python scripts—that do useful things with the database. They demonstrate that the system actually works and can answer questions.

#### [analyze_data.py](.cursor/skills/analyze_data.py)
**Purpose**: Query the database without writing SQL manually.

**Why I wrote it**: I kept opening SQLite in the terminal to debug. Now I can ask "what was the hottest day in Vancouver?" and get a clean JSON response.

**Supported queries**:
- `temperature_summary`: Min/max/avg per city
- `event_summary`: Event counts by type and severity
- `recent_extremes`: High-impact events
- `city_comparison`: Weather side-by-side across cities

**Example**:
```bash
python .cursor/skills/analyze_data.py --query city_comparison --hours 24
```

#### [replay_detection.py](.cursor/skills/replay_detection.py)
**Purpose**: Replay historical readings through the detector to validate thresholds.

**Why I built it**: I wanted to answer "if I change Vancouver's extreme_heat threshold from 26°C to 27°C, what would have fired differently last week?" This skill lets me test hypothetical changes against real (or simulated) data.

**Supported arguments**:
- `--city`: Filter by city
- `--limit`: Max readings to process
- `--days`: Only recent N days
- `--db`: Database path

**Example**:
```bash
python .cursor/skills/replay_detection.py --city Vancouver --days 7
```

**Output**: Shows each reading + what events fired + summary counts.

---

## CI/CD Pipeline

I set up GitHub Actions so every push to `main` automatically tests and builds the Docker image. This way I catch breaking changes before they get deployed.

### How It Works

**Test Job**
```
1. Spin up Ubuntu latest
2. Install Python 3.11 + pip install -r requirements.txt
3. Run pytest tests/ -v
4. Fail the pipeline if ANY test fails
```

This forces me to keep the tests passing. If something's broken, I find out immediately.

**Build Job** (runs only after Test passes)
```
1. Build Docker image: docker build -t watchagent:latest .
2. If build fails, the whole pipeline fails
3. Does NOT push to a registry (that's a future step)
```

This ensures the Docker image can actually be built with the code as-is.

### Verify Locally

Before pushing, I test locally to avoid CI failures:
```bash
pytest tests/ -v
docker build -t watchagent:latest .
```

---

## Troubleshooting

Ran into issues while testing locally? Here's what I discovered:

### API not responding
```bash
curl http://localhost:8000/health
```

If it fails, the poller probably crashed. Check the logs:
```bash
docker compose logs watchagent | tail -20
```

Look for ERROR messages from the poller (usually timeout from Open-Meteo).

### No readings appearing
Wait at least 5 minutes (that's the poll interval). Then check if readings made it into the database:
```bash
docker compose exec watchagent sqlite3 /data/watchagent.db "SELECT COUNT(*) FROM readings;"
```

If count is 0, either:
1. Open-Meteo API is timing out (check logs for WARNING messages)
2. Database isn't writable

### Events not appearing
Events only fire when readings meet thresholds. If weather is normal, nothing will trigger. Test with extreme conditions:
```bash
pytest tests/test_core.py::TestEventDetection::test_extreme_heat_detected -v
```

This proves detection logic works. Real data might just be too tame.

### Database permission error
The `/data` directory inside the container needs to be writable:
```bash
docker compose exec watchagent ls -la /data/
```

Should show `watchagent.db` with write permissions. If error, the volume isn't mounted correctly.

---

## Technology Choices

### FastAPI
I picked FastAPI because I wanted automatic OpenAPI documentation and solid testing support. Flask would've been simpler (less code) but FastAPI forces good structure. For an API that needs to be reviewed, that structure matters. Built-in async support and Pydantic validation are nice bonuses.

### SQLite
Single-file database, zero external services. Perfect for something running in one container. I considered PostgreSQL but that means deploying and managing another service. SQLite gets me ACID transactions and query capability without the overhead. Trade-off: doesn't scale to massive concurrency, but this isn't that workload.

### Docker Compose
Lets me define the entire environment in one file. Poller + API in same container, volume for persistence, easy `docker compose up`. Could've done Kubernetes but that's overkill for a fixed-interval poller. Compose is just right for "deploy this locally and in production the same way."

### City-Specific Thresholds
This was a deliberate design choice. I could've used one global threshold ("anything > 30°C is extreme") but that would be wrong. Weather is regional. I researched climate normals for each city and set thresholds that match local patterns. More complex code but way better signal.

### Threading for the Poller
I run the poller in a daemon thread in the main process alongside the API server. I considered separate containers but that means orchestrating two services for something that just needs to poll on a fixed interval. Threads are simpler. Trade-off: if the poller thread crashes it won't restart automatically, but logging will show it. That's acceptable for this scope.

---

## Deployment

### Local Setup
```bash
docker compose up --build
```

This uses a named volume `watchagent_data` so the database persists across container restarts. If I need to start fresh:
```bash
docker compose down -v  # -v deletes the volume
docker compose up
```

### Production Notes (Not Implemented, But Worth Thinking About)

If this were a real production service, I'd:
1. **Separate the poller and API** into different containers so they can scale independently
2. **Use PostgreSQL** instead of SQLite for concurrent read/write access
3. **Add API authentication** (currently `/readings` and `/events` are public)
4. **Set up monitoring** to alert if the poller stops or detection goes weird
5. **Add event webhooks** so external systems can subscribe to alerts
6. **Cache frequently accessed queries** (recent readings, event summaries)

For now, running everything in one container keeps deployment simple.

## File Structure

```
WatchAgent/
├── src/
│   ├── __init__.py
│   ├── main.py              # Entry point; starts API + poller
│   ├── api.py               # FastAPI endpoints
│   ├── database.py          # SQLite wrapper + queries
│   ├── poller.py            # Open-Meteo polling logic
│   └── detector.py          # Event detection logic
├── tests/
│   └── test_core.py         # Unit tests (dedup, detection, API)
├── .cursor/
│   ├── rules/
│   │   ├── 01-error-handling.md     # Error & retry patterns
│   │   └── 02-data-structure.md     # Schema & API contract
│   ├── agents/
│   │   └── event-detection-reviewer.md
│   └── skills/
│       ├── analyze_data.py          # Data analysis queries
│       └── replay_detection.py      # Replay readings through detection
├── .github/
│   └── workflows/
│       └── ci.yml                   # GitHub Actions pipeline
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
├── .env.example
└── README.md
```

---

## Future Improvements

- **Multi-city event correlation**: Detect regional weather patterns (e.g., cold front across all three)
- **Event webhooks**: POST to a configured URL when high-severity events occur
- **Forecasting**: Fetch multi-day forecast and detect predicted extremes
- **Alerting thresholds**: Per-user customization of what counts as "notable"
- **Time-series visualization**: Dashboard showing temperature/wind/event timelines

