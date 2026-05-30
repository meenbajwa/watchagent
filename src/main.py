"""
Main entry point for WatchAgent service.
Starts both the API server and the weather poller.
"""
import logging
import os
import sys
from threading import Thread
from pathlib import Path

# Ensure src is importable
sys.path.insert(0, str(Path(__file__).parent))

from database import Database
from poller import WeatherPoller
from detector import EventDetector
from api import app, init_api
import uvicorn

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def main():
    """Start API server and weather poller."""
    
    # Initialize database
    db_path = os.getenv("DB_PATH", "/data/watchagent.db")
    logger.info(f"Using database at: {db_path}")
    
    db = Database(db_path=db_path)
    
    # Initialize API with database
    init_api(db)
    
    # Create detector and poller
    detector = EventDetector(db)
    poller = WeatherPoller(
        db=db,
        detector=detector,
        poll_interval=int(os.getenv("POLL_INTERVAL", "300")),  # Default 5 minutes
        max_retries=3
    )
    
    # Start poller in background thread. Running poller in a daemon thread
    # alongside uvicorn in the main thread keeps the process simple — one
    # container, one process. Trade-off: if the poller thread dies silently,
    # nothing restarts it. Acceptable for this scope; production would use
    # separate containers or a process manager.
    poller_thread = Thread(target=poller.run_continuous, daemon=True)
    poller_thread.start()
    logger.info("Weather poller started in background")
    
    # Start API server
    port = int(os.getenv("API_PORT", "8000"))
    host = os.getenv("API_HOST", "0.0.0.0")
    
    logger.info(f"Starting API server on {host}:{port}")
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
