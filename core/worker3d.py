"""Process-pool worker for heavy 3D geometry (build123d / Open Cascade).

The extrude/revolve/export work is CPU- and memory-heavy and runs inside the
Open Cascade kernel (build123d). Running it in *separate OS processes* means:

  * a crash, OOM, or assertion failure inside the kernel can never take down the
    web server, and
  * the heavy compute doesn't share the event loop's thread, so the server stays
    responsive even while a big boolean/STEP export is running.

The pool is created lazily (on first use) so importing this module — or the
server — is cheap and build123d is never imported until a real job runs.

If multiprocessing can't be set up, or a worker process dies (BrokenProcessPool),
the call transparently falls back to an in-process thread so the request still
succeeds.
"""

from __future__ import annotations

import asyncio
import atexit
import multiprocessing
import os
import pickle
from concurrent.futures import ProcessPoolExecutor
try:
    from concurrent.futures.process import BrokenProcessPoolError
except ImportError:  # Python 3.14+ renamed it to BrokenProcessPool
    from concurrent.futures.process import BrokenProcessPool as BrokenProcessPoolError

# A small, conservative default: each worker loads the Open Cascade kernel
# (~hundreds of MB), so we don't want one process per CPU core by default.
_DEFAULT_WORKERS = 2

# Per-job timeout (seconds) for worker calls. A hung kernel op would otherwise
# block the request forever; configurable via SKETCHDIY_3D_TIMEOUT.
_DEFAULT_TIMEOUT_SECS = 120.0

# None  => not yet created
# False => creation failed (use thread fallback)
# Executor => ready to use
_executor = None


class JobTimeoutError(Exception):
    """Raised when a 3D job exceeds SKETCHDIY_3D_TIMEOUT."""


def _job_timeout() -> float:
    raw = os.environ.get("SKETCHDIY_3D_TIMEOUT")
    if not raw:
        return _DEFAULT_TIMEOUT_SECS
    try:
        return max(1.0, float(raw))
    except (TypeError, ValueError):
        return _DEFAULT_TIMEOUT_SECS


def shutdown():
    """Shut the process pool down, releasing the Open Cascade kernel loaded in
    each worker. Safe to call multiple times and from atexit; clears the cached
    executor so a later job recreates it if the process keeps running.
    """
    global _executor
    ex = _executor
    if ex and ex is not False:
        try:
            ex.shutdown(wait=False, cancel_futures=True)
        except Exception:  # noqa: BLE001 - best-effort teardown
            pass
    _executor = None


# Release worker processes (each holds a heavy Open Cascade kernel) on exit so
# they aren't orphaned across dev reloads or an unclean shutdown.
atexit.register(shutdown)


def _make_executor() -> ProcessPoolExecutor:
    n = int(os.environ.get("SKETCHDIY_3D_WORKERS", _DEFAULT_WORKERS))
    n = max(1, n)
    ctx = multiprocessing.get_context("spawn")
    return ProcessPoolExecutor(max_workers=n, mp_context=ctx)


def get_executor():
    """Return the shared ProcessPoolExecutor, creating it on first use.

    Returns None when multiprocessing is unavailable, so callers fall back to a
    thread.
    """
    global _executor
    if _executor is None:
        try:
            _executor = _make_executor()
        except Exception:  # noqa: BLE001 - no multiprocessing / spawn unsupported
            _executor = False
    return _executor or None


def executor_kind() -> str:
    """'process', 'thread' (fallback), or 'none' — handy for status displays."""
    ex = get_executor()
    return "process" if ex is not None else "thread"


async def run(func, *args, timeout: float | None = None):
    """Run a picklable callable in the process pool, falling back to a thread.

    `func` and its `args` must be picklable (plain functions in `core.part3d`
    plus dict/list/primitive data — which is exactly what the 3D endpoints pass).

    The job is bounded by `timeout` (defaults to SKETCHDIY_3D_TIMEOUT, 120s). A
    timed-out job raises `JobTimeoutError` — it is *not* retried in-thread, since
    the same kernel op would just hang the event-loop thread too.

    Validation errors raised by the worker (ValueError) propagate unchanged so the
    endpoint can map them to HTTP 400. Two kinds of failure are retried on a fresh
    in-process thread (so the request still completes): a dead worker pool
    (`BrokenProcessPoolError`) and a payload that couldn't be (un)serialised
    (`PicklingError`). Any other error is treated as a genuine logic/runtime
    failure and propagates without retry — re-running the identical job in a
    thread would just fail the same way and waste time.
    """
    if timeout is None:
        timeout = _job_timeout()
    loop = asyncio.get_running_loop()
    ex = get_executor()
    if ex is None:
        # Thread fallback (multiprocessing unavailable). Still bound by timeout.
        return await asyncio.wait_for(asyncio.to_thread(func, *args), timeout)
    try:
        fut = ex.submit(func, *args)
        return await asyncio.wait_for(asyncio.wrap_future(fut), timeout)
    except asyncio.TimeoutError:
        # Don't retry a timed-out kernel op (it would just hang the thread too).
        raise JobTimeoutError(
            f"3D job exceeded the {timeout:.0f}s timeout (SKETCHDIY_3D_TIMEOUT)"
        ) from None
    except (BrokenProcessPoolError, pickle.PicklingError):
        # The pool died or the payload couldn't be (un)serialised. Drop the dead
        # pool and retry once on a fresh in-process thread so the request completes.
        global _executor
        _executor = None
        return await asyncio.wait_for(asyncio.to_thread(func, *args), timeout)
    except ValueError:
        # user-facing validation error from part3d: re-raise, no retry
        raise
    # Any other exception (RuntimeError from build123d, OCP crashes surfaced as a
    # generic Exception, etc.) propagates without a thread fallback.
