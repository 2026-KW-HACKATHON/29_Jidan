"""The periodic job runner and the composed application lifespan."""
import asyncio
import logging
import threading

import pytest

from app import lifespan as app_lifespan
from app import periodic
from app.oauth_cleanup import oauth_cleanup_lifespan


def test_periodic_job_runs_at_start_retries_failures_and_never_logs_their_text(caplog):
    calls, delays = [], []

    def job():
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("password=hunter2")

    async def sleep(seconds):
        delays.append(seconds)
        if len(delays) == 3:
            raise asyncio.CancelledError

    async def run():
        with pytest.raises(asyncio.CancelledError):
            await periodic.run_periodically(periodic.PeriodicJob("demo", 7, job), sleep=sleep)

    with caplog.at_level(logging.ERROR, logger="jidan.errors"):
        asyncio.run(run())
    assert len(calls) == 3 and delays == [7, 7, 7]
    assert "demo" in caplog.text and "hunter2" not in caplog.text


def test_periodic_jobs_are_cancelled_when_the_block_exits():
    started = threading.Event()

    def job():
        started.set()

    async def run():
        jobs = [periodic.PeriodicJob("a", 3600, job), periodic.PeriodicJob("b", 3600, job)]
        async with periodic.periodic_jobs(jobs):
            await asyncio.to_thread(started.wait, 5)
        return [t for t in asyncio.all_tasks() if t.get_name().startswith("periodic:")]

    assert asyncio.run(run()) == []
    assert started.is_set()


def test_lifespan_composes_registered_lifespans_in_order(monkeypatch):
    from contextlib import asynccontextmanager

    events = []

    def tracker(name):
        @asynccontextmanager
        async def factory(_app):
            events.append(f"start {name}")
            yield
            events.append(f"stop {name}")
        return factory

    monkeypatch.setattr(app_lifespan, "LIFESPANS", [tracker("one"), tracker("two")])

    async def run():
        async with app_lifespan.lifespan(None):
            events.append("serving")

    asyncio.run(run())
    assert events == ["start one", "start two", "serving", "stop two", "stop one"]


def test_default_lifespans_keep_oauth_cleanup():
    assert app_lifespan.LIFESPANS[0] is oauth_cleanup_lifespan
    assert app_lifespan.periodic_jobs_lifespan in app_lifespan.LIFESPANS


@pytest.mark.parametrize(("raw", "enabled"), [(None, True), ("", True), ("on", True), ("off", False)])
def test_background_jobs_setting(monkeypatch, raw, enabled):
    if raw is None:
        monkeypatch.delenv("BACKGROUND_JOBS", raising=False)
    else:
        monkeypatch.setenv("BACKGROUND_JOBS", raw)
    assert app_lifespan.background_jobs_enabled() is enabled


@pytest.mark.parametrize("raw", ["OFF", "false", "0", "no", "of"])
def test_malformed_background_jobs_setting_stops_startup(monkeypatch, raw):
    monkeypatch.setenv("BACKGROUND_JOBS", raw)
    monkeypatch.setattr(app_lifespan, "LIFESPANS", [])

    async def run():
        async with app_lifespan.lifespan(None):
            pass

    with pytest.raises(app_lifespan.BackgroundJobsConfigurationError):
        asyncio.run(run())


@pytest.mark.parametrize(("raw", "runs"), [("off", 0), ("on", 1)])
def test_periodic_jobs_start_only_when_enabled(monkeypatch, raw, runs):
    calls = []
    ran = threading.Event()

    def job():
        calls.append(1)
        ran.set()

    monkeypatch.setenv("BACKGROUND_JOBS", raw)
    monkeypatch.setattr(app_lifespan, "PERIODIC_JOBS", [periodic.PeriodicJob("probe", 3600, job)])

    async def run():
        async with app_lifespan.periodic_jobs_lifespan(None):
            await asyncio.to_thread(ran.wait, 1 if runs else 0.2)

    asyncio.run(run())
    assert len(calls) == runs


def test_tests_run_without_background_jobs():
    import os

    assert os.environ["BACKGROUND_JOBS"] == "off"
