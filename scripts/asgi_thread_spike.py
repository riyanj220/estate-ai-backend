"""Spike for AD-14: do synchronous Django views run concurrently under Daphne?

Serves one sync view (time.sleep) and one async view (asyncio.sleep) from a throwaway
Django project, fires concurrent requests and prints the observed overlap as JSON.
Exits 2 if Daphne fails to start, 1 if sync views were serialised, else 0.
Usage: uv run python scripts/asgi_thread_spike.py
"""

import json
import os
import socket
import subprocess
import sys
import tempfile
import textwrap
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

DELAY = 1.0
CALLS = 4

APP = f"""
import asyncio
import time

from django.conf import settings
from django.core.asgi import get_asgi_application
from django.http import HttpResponse
from django.urls import path

settings.configure(
    DEBUG=False, ALLOWED_HOSTS=["*"], ROOT_URLCONF=__name__, SECRET_KEY="spike-only", USE_TZ=True
)


def sync_view(request):
    time.sleep({DELAY})
    return HttpResponse("sync")


async def async_view(request):
    await asyncio.sleep({DELAY})
    return HttpResponse("async")


urlpatterns = [path("sync", sync_view), path("async", async_view)]
application = get_asgi_application()
"""


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def fetch(url: str) -> None:
    with urllib.request.urlopen(url, timeout=30) as resp:  # noqa: S310
        resp.read()


def burst(base: str, paths: list[str]) -> float:
    start = time.monotonic()
    with ThreadPoolExecutor(max_workers=len(paths)) as pool:
        list(pool.map(fetch, [base + p for p in paths]))
    return time.monotonic() - start


def main() -> int:
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    with tempfile.TemporaryDirectory() as tmp:
        Path(tmp, "spike_app.py").write_text(textwrap.dedent(APP))
        env = {**os.environ, "PYTHONPATH": tmp}
        proc = subprocess.Popen(  # noqa: S603
            [sys.executable, "-m", "daphne", "-b", "127.0.0.1", "-p", str(port)]
            + ["spike_app:application"],
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            ready = False
            for _ in range(100):
                if proc.poll() is not None:
                    break
                try:
                    fetch(base + "/async")
                    ready = True
                    break
                except OSError:
                    time.sleep(0.1)
            if not ready:
                proc.kill()
                _, err = proc.communicate()
                print(f"Daphne did not become ready:\n{err}", file=sys.stderr)
                return 2
            sync_total = burst(base, ["/sync"] * CALLS)
            async_total = burst(base, ["/async"] * CALLS)
            mixed_total = burst(base, ["/sync"] * 2 + ["/async"] * 2)
        finally:
            if proc.poll() is None:
                proc.terminate()
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
            for stream in (proc.stdout, proc.stderr):
                if stream:
                    stream.close()
    result = {
        "delay_seconds": DELAY,
        "calls": CALLS,
        "sync_concurrent_total": round(sync_total, 2),
        "async_concurrent_total": round(async_total, 2),
        "mixed_2_sync_2_async_total": round(mixed_total, 2),
        "sync_views_serialised": sync_total > DELAY * (CALLS - 0.5),
        "async_views_overlap": async_total < DELAY * 2,
    }
    print(json.dumps(result, indent=2))
    if result["sync_views_serialised"]:
        print("Sync views were serialised (exit 1).", file=sys.stderr)
    return 1 if result["sync_views_serialised"] else 0


if __name__ == "__main__":
    sys.exit(main())
