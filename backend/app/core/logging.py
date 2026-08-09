"""`structlog` configuration for the whole application.

Called once, at application startup (`main.py`). After configuration, any
module obtains a logger with `structlog.get_logger(__name__)`.
"""

import logging
import sys

import structlog


def configure_logging(debug: bool = False) -> None:
    """Configures `structlog` with structured JSON output.

    Args:
        debug: If True, the minimum log level is DEBUG; otherwise INFO.
    """
    log_level = logging.DEBUG if debug else logging.INFO

    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=log_level,
    )

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.dev.ConsoleRenderer() if debug else structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )
