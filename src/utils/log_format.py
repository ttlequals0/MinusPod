"""Log formatters and the console redaction filter."""
import copy
import json
import logging
import os
import socket
import sys

from utils.http import redact_log_text, report_redaction_failure

_HOSTNAME = os.environ.get('HOSTNAME') or socket.gethostname()
# Record attribute set through logging's extra; only the value True keeps the URL query.
KEEP_URL_QUERY = 'keep_url_query'


def keeps_url_query(record: logging.LogRecord) -> bool:
    return getattr(record, KEEP_URL_QUERY, False) is True


def _flatten(text: str) -> str:
    return text.replace('\r\n', '\\n').replace('\n', '\\n').replace('\r', '\\n')


def redact_message(message: str, keep_query: bool = False) -> str:
    """Redact as the one-line text format does, keeping line breaks only where that gives the same result."""
    redacted = redact_log_text(message, keep_query)
    flat = _flatten(message)
    if flat == message:
        return redacted
    flat_redacted = redact_log_text(flat, keep_query)
    return redacted if _flatten(redacted) == flat_redacted else flat_redacted


def _raw_message(record: logging.LogRecord) -> str:
    # Args are interpolated only when present, so the common no-args record is not built twice.
    return record.getMessage() if record.args else str(record.msg)


class ScrubbingFormatter(logging.Formatter):
    """Plain formatter that redacts URL query strings from message and traceback."""

    def format(self, record: logging.LogRecord) -> str:
        keep_query = keeps_url_query(record)
        message = _raw_message(record)
        if '\n' in message or '\r' in message:
            record = copy.copy(record)
            record.msg = redact_message(message, keep_query)
            record.args = ()
        return redact_log_text(super().format(record), keep_query)


class SingleLineFormatter(ScrubbingFormatter):
    """Text formatter that keeps one record on one line so Docker does not split it."""

    def format(self, record: logging.LogRecord) -> str:
        message = _raw_message(record)
        if '\n' not in message and '\r' not in message:
            return super().format(record)
        flat = copy.copy(record)
        flat.msg = _flatten(message)
        flat.args = ()
        return super().format(flat)


class JSONFormatter(logging.Formatter):
    """JSON log formatter with hostname, pid and request_id for log aggregators."""

    def format(self, record: logging.LogRecord) -> str:
        keep_query = keeps_url_query(record)
        log_data = {
            'timestamp': self.formatTime(record, self.datefmt),
            'level': record.levelname,
            'logger': record.name,
            'message': redact_message(record.getMessage(), keep_query),
            'hostname': _HOSTNAME,
            'pid': os.getpid(),
        }

        for attr in ('episode_id', 'slug', 'request_id'):
            value = getattr(record, attr, None)
            if value is not None:
                log_data[attr] = value

        if record.exc_info:
            log_data['exception'] = redact_log_text(
                self.formatException(record.exc_info), keep_query)

        return json.dumps(log_data)


class QueryStringRedactingFilter(logging.Filter):
    """Redact message, traceback and stack text in place, whatever formatter runs later."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            keep_query = keeps_url_query(record)
            message = record.getMessage()
            redacted = redact_message(message, keep_query)
            if redacted != message:
                # Flattened in place: handlers later on the record see the redacted text.
                record.msg = redacted
                record.args = ()
            if record.exc_info and not record.exc_text:
                # Formatter.format reuses a preset exc_text, so the traceback renders once.
                record.exc_text = logging.Formatter().formatException(record.exc_info)
            if record.exc_text:
                record.exc_text = redact_log_text(record.exc_text, keep_query)
            if record.stack_info:
                record.stack_info = redact_log_text(record.stack_info, keep_query)
        except Exception as exc:
            report_redaction_failure(exc)
        return True


def _redacted_handle_error(self, record: logging.LogRecord) -> None:
    """Handler.handleError without the raw record args, which can carry a secret."""
    if not logging.raiseExceptions:
        return
    try:
        sys.stderr.write(
            f"Logging error in {record.name} at {record.pathname}:{record.lineno}: "
            f"{type(sys.exc_info()[1]).__name__}; message: {redact_log_text(record.msg)}\n")
    except Exception:
        pass


def guard_handler_errors() -> None:
    """Make every handler report its own failures as one redacted line."""
    logging.Handler.handleError = _redacted_handle_error


def make_formatter(log_format: str | None = None) -> logging.Formatter:
    """Build the formatter for LOG_FORMAT (text or json); reads the env var when unset."""
    if log_format is None:
        log_format = os.environ.get('LOG_FORMAT', 'text')
    if log_format.lower() == 'json':
        return JSONFormatter(datefmt='%Y-%m-%dT%H:%M:%S')
    return SingleLineFormatter(
        '[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S')
