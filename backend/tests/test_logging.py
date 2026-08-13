"""Tests that a logged exception actually carries its traceback.

Written after a real incident: a schema drift broke every book fetch, and
the only thing on the console was

    {"job_id": 39, "title": "...", "exc_info": true,
     "event": "book_data_fetch_failed", "level": "error"}

`exc_info: true` is the *flag* `logger.exception()` sets, serialized
literally. The traceback was never rendered, because the processor chain
had no `format_exc_info` ahead of `JSONRenderer`.

That is worst precisely where this app relies on it. Several `except`
blocks are deliberately non-fatal — a catalog outage must not fail a scan,
a source bug must not fail a job — and every one of them is a place where
the *only* record of what went wrong is the log line. Swallowing an
exception is a reasonable design choice; swallowing it silently is not.
"""

import json
from collections.abc import Iterator

import pytest
import structlog

from app.core.logging import configure_logging


@pytest.fixture(autouse=True)
def restore_logging_config() -> Iterator[None]:
    """Restores structlog's global configuration after each test.

    `configure_logging` mutates process-wide state, so without this the
    first test here would silently change how every later test logs.
    """
    try:
        yield
    finally:
        structlog.reset_defaults()
        configure_logging(debug=False)


def _log_a_caught_exception(logger_name: str = "test") -> None:
    """Logs an exception the way the app's non-fatal `except` blocks do."""
    logger = structlog.get_logger(logger_name)
    try:
        raise ValueError("no such column: books.summary_json")
    except ValueError:
        logger.exception("book_data_fetch_failed", job_id=39, title="Dune")


def test_json_logs_carry_the_rendered_traceback(capsys: pytest.CaptureFixture[str]) -> None:
    """The regression test: the traceback reaches the log, not just a flag."""
    configure_logging(debug=False)

    _log_a_caught_exception()

    payload = json.loads(capsys.readouterr().out.strip().splitlines()[-1])

    assert payload["event"] == "book_data_fetch_failed"
    assert payload["job_id"] == 39

    # The flag must have been consumed into a rendered string, not emitted
    # as `"exc_info": true`.
    assert payload.get("exc_info") is not True
    assert "exception" in payload, "the traceback is missing from the log record"

    traceback = payload["exception"]
    assert "ValueError" in traceback
    assert "no such column: books.summary_json" in traceback
    assert "Traceback" in traceback


def test_the_cause_is_findable_in_the_raw_output(capsys: pytest.CaptureFixture[str]) -> None:
    """The practical assertion: the reason appears in what a developer sees.

    Whatever the field is called, the message that explains the failure has
    to be somewhere in the console output — that is the whole point.
    """
    configure_logging(debug=False)

    _log_a_caught_exception()

    assert "no such column: books.summary_json" in capsys.readouterr().out


def test_ordinary_logs_are_still_plain_json(capsys: pytest.CaptureFixture[str]) -> None:
    """Adding exception rendering must not disturb non-exception logging."""
    configure_logging(debug=False)

    structlog.get_logger("test").info("book_cached", book_id=7, passages=4)

    payload = json.loads(capsys.readouterr().out.strip().splitlines()[-1])

    assert payload["event"] == "book_cached"
    assert payload["book_id"] == 7
    assert payload["level"] == "info"
    assert "exception" not in payload


def test_debug_mode_still_reports_the_cause(capsys: pytest.CaptureFixture[str]) -> None:
    """The console renderer path must show the exception too.

    It formats exceptions itself rather than through `format_exc_info`, so
    it is a genuinely separate path and worth asserting separately.
    """
    configure_logging(debug=True)

    _log_a_caught_exception()

    assert "no such column: books.summary_json" in capsys.readouterr().out
