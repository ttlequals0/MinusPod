"""Log formatters and the console redaction filter."""
import copy
import json
import logging
import os
import socket
import sys
import threading

from utils.http import redact_log_text, report_redaction_failure

_HOSTNAME = os.environ.get('HOSTNAME') or socket.gethostname()
# Record attribute set through logging's extra; only the value True keeps the URL query.
KEEP_URL_QUERY = 'keep_url_query'


def keeps_url_query(record: logging.LogRecord) -> bool:
    return getattr(record, KEEP_URL_QUERY, False) is True


def _flatten(text: str) -> str:
    return text.replace('\r\n', '\\n').replace('\n', '\\n').replace('\r', '\\n')


# Stands in for a line break while redacting: no redaction pass treats it differently from "\\n".
_BREAK = '\ue000\ue001'


def redact_message(message: str, keep_query: bool = False) -> str:
    """Redact as the one-line text format does, keeping each line break that redaction leaves intact."""
    flat = _flatten(message)
    if flat == message:
        return redact_log_text(message, keep_query)
    if _BREAK[0] in message or _BREAK[1] in message:
        return redact_log_text(flat, keep_query)
    marked = message.replace('\r\n', _BREAK).replace('\n', _BREAK).replace('\r', _BREAK)
    return redact_log_text(marked, keep_query).replace(_BREAK, '\n')


def _raw_message(record: logging.LogRecord) -> str:
    # Args are interpolated only when present, so the common no-args record is not built twice.
    return record.getMessage() if record.args else str(record.msg)


class ScrubbingFormatter(logging.Formatter):
    """Plain formatter that redacts URL query strings from message and traceback."""

    def format(self, record: logging.LogRecord) -> str:
        keep_query = keeps_url_query(record)
        head = copy.copy(record)
        head.msg = redact_message(_raw_message(record), keep_query)
        head.args = ()
        head.exc_info = head.exc_text = head.stack_info = None
        text = redact_log_text(super().format(head), keep_query)
        # Tracebacks and stacks never keep a query: the record's mark covers only its message.
        if record.exc_text or record.exc_info:
            text += '\n' + redact_message(record.exc_text or self.formatException(record.exc_info))
        if record.stack_info:
            text += '\n' + redact_message(self.formatStack(record.stack_info))
        return text


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
            log_data['exception'] = redact_message(self.formatException(record.exc_info))

        return json.dumps(log_data)


class QueryStringRedactingFilter(logging.Filter):
    """Redact message, traceback and stack text in place, whatever formatter runs later."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:
            # The handler reports a bad format string itself through handleError.
            return True
        try:
            keep_query = keeps_url_query(record)
            redacted = redact_message(message, keep_query)
            if redacted != message:
                # Flattened in place: handlers later on the record see the redacted text.
                record.msg = redacted
                record.args = ()
            if record.exc_info and not record.exc_text:
                # Formatter.format reuses a preset exc_text, so the traceback renders once.
                record.exc_text = logging.Formatter().formatException(record.exc_info)
            if record.exc_text:
                record.exc_text = redact_message(record.exc_text)
            if record.stack_info:
                record.stack_info = redact_message(record.stack_info)
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


def _log_uncaught_thread(args) -> None:
    if args.exc_type is SystemExit:
        return
    name = args.thread.name if args.thread is not None else 'unknown'
    logging.getLogger('podcast.uncaught').error(
        f"Uncaught exception in thread {name}",
        exc_info=(args.exc_type, args.exc_value, args.exc_traceback))


def _log_uncaught(exc_type, exc_value, exc_traceback) -> None:
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc_value, exc_traceback)
        return
    logging.getLogger('podcast.uncaught').critical(
        "Uncaught exception", exc_info=(exc_type, exc_value, exc_traceback))


def install_exception_hooks() -> None:
    """Route uncaught exceptions through logging so the redaction filter sees their text."""
    threading.excepthook = _log_uncaught_thread
    sys.excepthook = _log_uncaught


def make_formatter(log_format: str | None = None) -> logging.Formatter:
    """Build the formatter for LOG_FORMAT (text or json); reads the env var when unset."""
    if log_format is None:
        log_format = os.environ.get('LOG_FORMAT', 'text')
    if log_format.lower() == 'json':
        return JSONFormatter(datefmt='%Y-%m-%dT%H:%M:%S')
    return SingleLineFormatter(
        '[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S')
