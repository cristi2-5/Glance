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

    # The Azure SDK logs every HTTP request and response, headers included,
    # at INFO. The queue worker polls the queue every 1-10 s, so an *idle*
    # worker wrote ~190 lines a minute — noise locally, and in Azure every
    # line is billed Log Analytics ingestion. Its warnings and errors still
    # come through; our own `storage.py` logs the events that matter.
    logging.getLogger("azure").setLevel(logging.WARNING)

    # `format_exc_info` turns the `exc_info=True` that `logger.exception()`
    # sets into a rendered traceback string. Without it `JSONRenderer`
    # serializes the flag literally — `"exc_info": true` — and the actual
    # exception is silently dropped. Every `logger.exception` call in the
    # app reports the failure but not its cause, which is worst exactly
    # where it matters: the deliberately non-fatal `except` blocks
    # (`_fetch_book_data`, the cover pipeline) that keep a request alive
    # when something downstream breaks.
    #
    # Only on the JSON path: `ConsoleRenderer` formats exceptions itself,
    # with colours and better framing, and structlog's docs are explicit
    # that the two should not be combined.
    processors: list[structlog.typing.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
    ]
    if debug:
        processors.append(structlog.dev.ConsoleRenderer())
    else:
        processors.append(structlog.processors.format_exc_info)
        processors.append(structlog.processors.JSONRenderer())

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )
