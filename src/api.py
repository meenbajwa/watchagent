"""
FastAPI application exposing weather readings and events.
"""
from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse
from typing import Optional, List, Dict, Any
import logging

logger = logging.getLogger(__name__)

app = FastAPI(title="WatchAgent", version="1.0.0")

# These will be set by main.py
_db = None


def init_api(db):
    """Initialize API with database instance."""
    global _db
    _db = db


@app.get("/health")
async def health():
    """Health check endpoint."""
    if not _db:
        return JSONResponse({"status": "error", "message": "Database not initialized"}, status_code=500)
    
    return {
        "status": "ok",
        "readings_stored": _db.count_readings(),
        "events_stored": _db.count_events()
    }


@app.get("/readings")
async def get_readings(
    city: Optional[str] = Query(None, description="Filter by city name"),
    limit: int = Query(50, ge=1, le=1000, description="Number of readings to return")
):
    """
    Get recent weather readings.
    
    Query Parameters:
    - city: Optional city filter (Ottawa, Toronto, or Vancouver)
    - limit: Number of readings (default 50, max 1000)
    
    Returns most recent readings first.
    """
    if not _db:
        return JSONResponse({"error": "Database not initialized"}, status_code=500)
    
    readings = _db.get_readings(city=city, limit=limit)
    
    return {
        "readings": readings,
        "count": len(readings),
        "filter": {"city": city, "limit": limit}
    }


@app.get("/events")
async def get_events(
    city: Optional[str] = Query(None, description="Filter by city name"),
    limit: int = Query(50, ge=1, le=1000, description="Number of events to return")
):
    """
    Get detected notable events.
    
    Query Parameters:
    - city: Optional city filter (Ottawa, Toronto, or Vancouver)
    - limit: Number of events (default 50, max 1000)
    
    Returns most recent events first.
    """
    if not _db:
        return JSONResponse({"error": "Database not initialized"}, status_code=500)
    
    events = _db.get_events(city=city, limit=limit)
    
    return {
        "events": events,
        "count": len(events),
        "filter": {"city": city, "limit": limit}
    }
