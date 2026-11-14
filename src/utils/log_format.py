"""Log formatters and the console redaction filter."""
import copy
import json
import logging
import os
import socket
import sys
import threading

from utils.http import MAX_REDACT_CHARS, cap_log_text, redact_log_text, report_redaction_failure

_HOSTNAME = os.environ.get('HOSTNAME') or socket.gethostname()
# Record attribute set through logging's extra; only the value True keeps the URL query.
KEEP_URL_QUERY = 'keep_url_query'
# Record attribute set once message, traceback and stack text are redacted in place.
REDACTED_MARK = '_text_redacted'


def keeps_url_query(record: logging.LogRecord) -> bool:
    return getattr(record, KEEP_URL_QUERY, False) is True


def _flatten(text: str) -> str:
    return text.replace('\r\n', '\\n').replace('\n', '\\n').replace('\r', '\\n')


def _raw_message(record: logging.LogRecord) -> str:
    # Args are interpolated only when present, so the common no-args record is not built twice.
    return record.getMessage() if record.args else str(record.msg)


def _join_line(text: str, more: str) -> str:
    # Same layout as logging.Formatter.format: a newline only when the text does not end in one.
    return text + more if text[-1:] == '\n' else f"{text}\n{more}"


def _ends_with_message(text: str, message: str) -> bool:
    """Whether the formatted line's prefix is clean, so the redacted message need not be scanned again."""
    prefix = text[:len(text) - len(message)]
    return text.endswith(message) and redact_log_text(prefix) == prefix


class ScrubbingFormatter(logging.Formatter):
    """Plain formatter that redacts URL query strings from message and traceback."""

    def format(self, record: logging.LogRecord) -> str:
        keep_query = keeps_url_query(record)
        redacted = getattr(record, REDACTED_MARK, False) is True
        head = copy.copy(record)
        head.msg = _raw_message(record) if redacted else redact_log_text(_raw_message(record), keep_query)
        head.args = ()
        head.exc_info = head.exc_text = head.stack_info = None
        text = super().format(head)
        if not redacted or not _ends_with_message(text, head.msg):
            text = redact_log_text(text, keep_query)
        # Tracebacks and stacks never keep a query: the record's mark covers only its message.
        if record.exc_text or record.exc_info:
            trace = record.exc_text or self.formatException(record.exc_info)
            text = _join_line(text, trace if redacted else redact_log_text(trace))
        if record.stack_info:
            stack = self.formatStack(record.stack_info)
            text = _join_line(text, stack if redacted else redact_log_text(stack))
        return cap_log_text(text)


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
            'message': (record.getMessage() if getattr(record, REDACTED_MARK, False) is True
                        else redact_log_text(record.getMessage(), keep_query)),
            'hostname': _HOSTNAME,
            'pid': os.getpid(),
        }

        for attr in ('episode_id', 'slug', 'request_id'):
            value = getattr(record, attr, None)
            if value is not None:
                log_data[attr] = value

        if record.exc_info:
            exception = redact_log_text(self.formatException(record.exc_info))
            # Message and exception share one cap; each keeps at least half of it.
            if len(log_data['message']) + len(exception) > MAX_REDACT_CHARS:
                log_data['message'] = cap_log_text(
                    log_data['message'], max(MAX_REDACT_CHARS - len(exception), MAX_REDACT_CHARS // 2))
                exception = cap_log_text(exception, MAX_REDACT_CHARS - len(log_data['message']))
            log_data['exception'] = exception

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
            redacted = redact_log_text(message, keep_query)
            if redacted != message:
                # Flattened in place: handlers later on the record see the redacted text.
                record.msg = redacted
                record.args = ()
            if record.exc_info and not record.exc_text:
                # Formatter.format reuses a preset exc_text, so the traceback renders once.
                record.exc_text = logging.Formatter().formatException(record.exc_info)
            if record.exc_text:
                record.exc_text = redact_log_text(record.exc_text)
            if record.stack_info:
                record.stack_info = redact_log_text(record.stack_info)
            setattr(record, REDACTED_MARK, True)
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
