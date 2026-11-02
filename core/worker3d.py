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
import multiprocessing
import os
from concurrent.futures import ProcessPoolExecutor

# A small, conservative default: each worker loads the Open Cascade kernel
# (~hundreds of MB), so we don't want one process per CPU core by default.
_DEFAULT_WORKERS = 2

# None  => not yet created
# False => creation failed (use thread fallback)
# Executor => ready to use
_executor = None


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

    Validation errors raised by the worker (ValueError) propagate unchanged so the
    endpoint can map them to HTTP 400. Any worker/death/pickling failure routes to
    an in-process thread so the request still completes.
    """
    loop = asyncio.get_running_loop()
    ex = get_executor()
    if ex is None:
        return await asyncio.to_thread(func, *args)
    try:
        fut = ex.submit(func, *args)
        return await asyncio.wait_for(asyncio.wrap_future(fut), timeout)
    except ValueError:
        # user-facing validation error from part3d: re-raise, no retry
        raise
    except Exception:  # noqa: BLE001 - BrokenProcessPool, pickling, etc.
        # Worker died or couldn't run the job: drop the pool and retry locally.
        global _executor
        _executor = None
        return await asyncio.to_thread(func, *args)
