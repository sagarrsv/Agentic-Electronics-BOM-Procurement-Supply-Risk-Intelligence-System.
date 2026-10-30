"""Bounded concurrent Mouser HTTP retrieval; unchanged deterministic offer evaluation."""
import asyncio
import os
import time
from collections import deque

import httpx
try:
    from langsmith import traceable
except ImportError:
    def traceable(*args, **kwargs):
        def decorate(fn):
            return fn
        return decorate
from tools.mouser_api import async_search_v2


class RollingRateLimiter:
    """Limits starts in any rolling 60-second window and spaces request starts."""
    def __init__(self, per_minute=30, start_interval=1.1, initial_used=1):
        self.per_minute = per_minute
        self.start_interval = start_interval
        self.starts = deque([time.monotonic()] * initial_used)
        self.lock = asyncio.Lock()
        self.last_start = 0.0

    async def acquire(self):
        async with self.lock:
            while True:
                now = time.monotonic()
                while self.starts and now - self.starts[0] >= 60:
                    self.starts.popleft()
                wait_interval = max(0.0, self.last_start + self.start_interval - now)
                wait_window = max(0.0, self.starts[0] + 60 - now) if len(self.starts) >= self.per_minute else 0.0
                delay = max(wait_interval, wait_window)
                if not delay:
                    self.starts.append(now)
                    self.last_start = now
                    return
                await asyncio.sleep(delay)


async def fetch_mouser_async(rows, manufacturer_mapping, max_concurrency=3,
                             start_interval=1.1, directory_request_count=1):
    """One exact request per unique (verified manufacturer, MPN), no retries."""
    keys = list(dict.fromkeys((manufacturer_mapping.get(
        str(row.get('MAKE') or row.get('manufacturer') or '').strip()),
        str(row.get('MPN') or row.get('mpn') or '').strip()) for row in rows))
    keys = [(make, mpn) for make, mpn in keys if make and mpn]
    limiter = RollingRateLimiter(start_interval=start_interval,
                                 initial_used=directory_request_count)
    semaphore = asyncio.Semaphore(max_concurrency)
    timeout = httpx.Timeout(20.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        async def one(key):
            async with semaphore:
                await limiter.acquire()
                try:
                    return key, await async_search_v2(client, *key)
                except Exception as exc:
                    return key, exc
        pairs = await asyncio.gather(*(one(key) for key in keys))
    return dict(pairs)


@traceable(name="Mouser Async Sourcing", run_type="chain")
def fetch_mouser_concurrent(rows, manufacturer_mapping):
    """Synchronous boundary inside supplier worker, keeping LangGraph unchanged."""
    concurrency = max(1, min(int(os.getenv('MOUSER_ASYNC_CONCURRENCY', '3')), 3))
    interval = max(1.0, float(os.getenv('MOUSER_MIN_REQUEST_INTERVAL_SECONDS', '1.1')))
    return asyncio.run(fetch_mouser_async(rows, manufacturer_mapping,
                                         max_concurrency=concurrency,
                                         start_interval=interval))
