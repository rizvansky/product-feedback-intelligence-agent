from __future__ import annotations

import argparse
import logging
import time
from threading import Event

from pfia.config import Settings, get_settings
from pfia.errors import PFIAError
from pfia.services import PFIAService, build_app_context


logger = logging.getLogger("pfia.worker")


def worker_loop(
    service: PFIAService,
    settings: Settings,
    *,
    once: bool = False,
    stop_event: Event | None = None,
    heartbeat_mode: str | None = None,
) -> None:
    """Run the polling loop that processes queued jobs.

    Args:
        service: Service facade that owns job execution logic.
        settings: Runtime settings controlling polling cadence.
        once: Whether to process at most one job and exit.
        stop_event: Optional event used to stop the loop gracefully.
        heartbeat_mode: Optional worker mode recorded in heartbeat state.
    """
    recovered = service.recover_inflight_jobs()
    if recovered:
        logger.warning("Recovered %s in-flight jobs after restart.", recovered)

    while True:
        if stop_event is not None and stop_event.is_set():
            return
        service.update_worker_heartbeat(mode=heartbeat_mode)
        try:
            processed_job_id = service.process_next_job()
            if processed_job_id:
                logger.info("Processed job %s", processed_job_id)
            elif once:
                return
        except PFIAError as exc:
            logger.error("Worker failed with %s: %s", exc.code, exc.message)
            if once:
                raise
        if once:
            return
        if stop_event is None:
            time.sleep(settings.worker_poll_interval_s)
        elif stop_event.wait(settings.worker_poll_interval_s):
            return


def run_worker(once: bool = False) -> None:
    """Build the default application context and start the standalone worker.

    Args:
        once: Whether to process at most one job and exit.
    """
    settings = get_settings()
    context = build_app_context(settings)
    service = PFIAService(context)
    worker_loop(service, settings, once=once, heartbeat_mode="standalone")


def main() -> None:
    """CLI entrypoint for the standalone worker process."""
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s"
    )
    parser = argparse.ArgumentParser(description="Run PFIA worker.")
    parser.add_argument(
        "--once", action="store_true", help="Process at most one queued job and exit."
    )
    args = parser.parse_args()
    run_worker(once=args.once)


if __name__ == "__main__":
    main()
