"""Log formatters and the console redaction filter."""
import copy
import json
import logging
import os
import socket

from utils.http import scrub_query_strings

_HOSTNAME = os.environ.get('HOSTNAME') or socket.gethostname()


class SingleLineFormatter(logging.Formatter):
    """Text formatter that keeps one record on one line.

    Docker splits a multi-line record into separate lines, and the
    continuation lines carry no level prefix, so a scraper re-sniffs their
    level from the text: a logged prompt body whose line reads "CRITICAL:"
    arrives as a critical entry. The record is copied rather than mutated,
    since the per-episode run log handler formats the same record and wants
    the real line breaks. An exc_info traceback is appended by the base
    formatter and keeps its own.
    """

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
    """JSON log formatter for structured logging.

    Outputs logs as JSON objects for easier parsing by log aggregators
    like Loki, Elasticsearch, or CloudWatch. Each line carries hostname
    and PID to make it easier to correlate across multi-worker
    deployments; request_id is populated from Flask ``g`` when the
    log call happens inside an active request context (see
    :func:`_attach_request_id`).
    """

    def format(self, record: logging.LogRecord) -> str:
        log_data = {
            'timestamp': self.formatTime(record, self.datefmt),
            'level': record.levelname,
            'logger': record.name,
            'message': record.getMessage(),
            'hostname': _HOSTNAME,
            'pid': os.getpid(),
        }

        for attr in ('episode_id', 'slug', 'request_id'):
            value = getattr(record, attr, None)
            if value is not None:
                log_data[attr] = value

        if record.exc_info:
            log_data['exception'] = self.formatException(record.exc_info)

        return json.dumps(log_data)


class QueryStringRedactingFilter(logging.Filter):
    """Redact URL query strings (signed CDN tokens) from the rendered message."""

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        if '://' in message:
            record.msg = scrub_query_strings(message)
            record.args = ()
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
