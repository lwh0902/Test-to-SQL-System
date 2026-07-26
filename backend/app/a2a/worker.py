"""A2A outbox lease worker — claims retryable/expired-lease messages and redelivers.

Usage (manual):
  python -m app.a2a.worker

Or started from FastAPI lifespan when A2A_WORKER_ENABLED=true.
"""

from __future__ import annotations

import asyncio
import logging
import os
import socket
import uuid
from typing import Optional

from app.a2a.contracts import A2AMessage
from app.a2a.dispatcher import Dispatcher

logger = logging.getLogger(__name__)


def default_worker_id() -> str:
    return os.getenv("A2A_WORKER_ID") or f"{socket.gethostname()}-{os.getpid()}-{uuid.uuid4().hex[:8]}"


class A2AWorker:
    def __init__(
        self,
        dispatcher: Optional[Dispatcher] = None,
        worker_id: Optional[str] = None,
        poll_interval: float = 2.0,
        batch_size: int = 5,
        lease_seconds: int = 30,
    ):
        self.dispatcher = dispatcher or Dispatcher()
        self.worker_id = worker_id or default_worker_id()
        self.poll_interval = float(os.getenv("A2A_WORKER_POLL_INTERVAL", poll_interval))
        self.batch_size = int(os.getenv("A2A_WORKER_BATCH_SIZE", batch_size))
        self.lease_seconds = int(os.getenv("A2A_WORKER_LEASE_SECONDS", lease_seconds))
        self._stop = asyncio.Event()
        self._task: Optional[asyncio.Task] = None

    async def run_once(self) -> int:
        """Claim and redeliver one batch. Returns number of messages attempted."""
        claimed = self.dispatcher.claim_with_lease(
            worker_id=self.worker_id,
            limit=self.batch_size,
            lease_seconds=self.lease_seconds,
        )
        if not claimed:
            return 0
        for row in claimed:
            try:
                msg = self.dispatcher.message_from_row(row)
                await self.dispatcher.deliver(msg, force=True)
                self.dispatcher.release_lease(row["id"], self.worker_id, success=True)
            except Exception as exc:
                logger.exception("worker redeliver failed id=%s", row.get("id"))
                self.dispatcher.release_lease(
                    row["id"],
                    self.worker_id,
                    success=False,
                    error=str(exc)[:500],
                )
        return len(claimed)

    async def loop(self) -> None:
        logger.info("A2A worker started id=%s", self.worker_id)
        while not self._stop.is_set():
            try:
                n = await self.run_once()
                if n == 0:
                    try:
                        await asyncio.wait_for(self._stop.wait(), timeout=self.poll_interval)
                    except asyncio.TimeoutError:
                        pass
            except Exception:
                logger.exception("A2A worker loop error")
                await asyncio.sleep(self.poll_interval)
        logger.info("A2A worker stopped id=%s", self.worker_id)

    def start_background(self) -> asyncio.Task:
        self._stop.clear()
        self._task = asyncio.create_task(self.loop(), name=f"a2a-worker-{self.worker_id}")
        return self._task

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            try:
                await asyncio.wait_for(self._task, timeout=5)
            except Exception:
                self._task.cancel()


async def _amain() -> None:
    logging.basicConfig(level=logging.INFO)
    # Ensure agents registered
    import app.agents.deep_diagnosis  # noqa: F401

    worker = A2AWorker()
    await worker.loop()


if __name__ == "__main__":
    asyncio.run(_amain())
