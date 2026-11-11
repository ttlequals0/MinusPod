"""Log formatters and the console redaction filter."""
import copy
import json
import logging
import os
import socket

from utils.http import redact_log_text, report_redaction_failure

_HOSTNAME = os.environ.get('HOSTNAME') or socket.gethostname()


class ScrubbingFormatter(logging.Formatter):
    """Plain formatter that redacts URL query strings from message and traceback."""

    def format(self, record: logging.LogRecord) -> str:
        return redact_log_text(super().format(record))


class SingleLineFormatter(ScrubbingFormatter):
    """Text formatter that keeps one record on one line so Docker does not split it."""

    def format(self, record: logging.LogRecord) -> str:
        # Args are interpolated only when present, so the common no-args
        # record is not built twice per line at DEBUG volume.
        message = record.getMessage() if record.args else str(record.msg)
        if '\n' not in message and '\r' not in message:
            return super().format(record)
        flat = copy.copy(record)
        flat.msg = (message.replace('\r\n', '\\n')
                    .replace('\n', '\\n').replace('\r', '\\n'))
        flat.args = ()
        return super().format(flat)


class JSONFormatter(logging.Formatter):
    """JSON log formatter with hostname, pid and request_id for log aggregators."""

    def format(self, record: logging.LogRecord) -> str:
        log_data = {
            'timestamp': self.formatTime(record, self.datefmt),
            'level': record.levelname,
            'logger': record.name,
            'message': redact_log_text(record.getMessage()),
            'hostname': _HOSTNAME,
            'pid': os.getpid(),
        }

        for attr in ('episode_id', 'slug', 'request_id'):
            value = getattr(record, attr, None)
            if value is not None:
                log_data[attr] = value

        if record.exc_info:
            log_data['exception'] = redact_log_text(
                self.formatException(record.exc_info))

        return json.dumps(log_data)


class QueryStringRedactingFilter(logging.Filter):
    """Redact message, traceback and stack text in place, whatever formatter runs later."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
            redacted = redact_log_text(message)
            if redacted != message:
                # Flattened in place: handlers later on the record see the redacted text.
                record.msg = redacted
                record.args = ()
            if record.exc_info and not record.exc_text:
                # Formatter.format reuses a preset exc_text, so the traceback renders once.
                record.exc_text = redact_log_text(
                    logging.Formatter().formatException(record.exc_info))
            if record.stack_info:
                record.stack_info = redact_log_text(record.stack_info)
        except Exception as exc:
            report_redaction_failure(exc)
        return True


def make_formatter(log_format: str | None = None) -> logging.Formatter:
    """Build the formatter for LOG_FORMAT (text or json); reads the env var when unset."""
    if log_format is None:
        log_format = os.environ.get('LOG_FORMAT', 'text')
    if log_format.lower() == 'json':
        return JSONFormatter(datefmt='%Y-%m-%dT%H:%M:%S')
    return SingleLineFormatter(
        '[%(asctime)s] [%(levelname)s] [%(name)s] %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S')
