from __future__ import annotations

import logging
import signal
import time

from app.config import get_settings
from app.db import SessionLocal, create_schema
from app.services import (
    advance_mock_simulations,
    claim_next_blind_run,
    mark_completed_bookings,
    process_notifications,
    seed_admin,
    sync_vpn_access,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("ntdrone.worker")
shutdown_requested = False


def _request_shutdown(signum: int, frame: object) -> None:
    global shutdown_requested
    shutdown_requested = True
    logger.info("shutdown requested signal=%s", signum)


def run_cycle() -> dict[str, object]:
    settings = get_settings()
    with SessionLocal() as db:
        vpn = sync_vpn_access(db, settings)
        claimed = None
        if settings.simulation_driver == "mock" or settings.simulator_api_url:
            claimed = claim_next_blind_run(db, settings, dispatch=True)
        mock_changed = advance_mock_simulations(db, settings)
        completed_bookings = mark_completed_bookings(db)
        notifications = process_notifications(db, settings)
        return {
            "vpn": vpn,
            "claimed_run": claimed.id if claimed else None,
            "mock_runs_changed": mock_changed,
            "completed_bookings": completed_bookings,
            "notifications": notifications,
        }


def main() -> None:
    settings = get_settings()
    settings.storage_root.mkdir(parents=True, exist_ok=True)
    create_schema()
    with SessionLocal() as db:
        seed_admin(db, settings)

    signal.signal(signal.SIGTERM, _request_shutdown)
    signal.signal(signal.SIGINT, _request_shutdown)
    logger.info("worker started poll_seconds=%s", settings.worker_poll_seconds)
    while not shutdown_requested:
        try:
            result = run_cycle()
            if (
                result["claimed_run"]
                or result["mock_runs_changed"]
                or result["completed_bookings"]
                or result["vpn"] != {"enabled": 0, "disabled": 0}
                or result["notifications"] != {"sent": 0, "failed": 0}
            ):
                logger.info("cycle result=%s", result)
        except Exception:
            logger.exception("worker cycle failed")
        time.sleep(max(1, settings.worker_poll_seconds))
    logger.info("worker stopped")


if __name__ == "__main__":
    main()
