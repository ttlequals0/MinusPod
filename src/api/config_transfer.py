"""Runtime configuration JSON transfer routes."""
import json
import logging
from io import BytesIO

from flask import request, send_file

from api import api, error_response, get_database, json_response, limiter, log_request
from config_transfer import (
    MAX_CONFIG_REQUEST_BYTES, ConfigTransferError, apply_config, build_preview,
    export_config,
)
from utils.app_version import APP_VERSION

logger = logging.getLogger('podcast.api')


@api.route('/system/config-backup', methods=['GET'])
@limiter.limit('5 per hour')
@log_request
def download_runtime_config():
    try:
        payload = export_config(get_database(), APP_VERSION)
        body = json.dumps(payload, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    except ConfigTransferError as exc:
        return error_response(str(exc), exc.status)
    except Exception:
        logger.exception('Runtime configuration export failed')
        return error_response('Runtime configuration could not be exported', 500)
    response = send_file(
        BytesIO(body), mimetype='application/json', as_attachment=True,
        download_name='minuspod-runtime-config.json',
        max_age=0,
    )
    response.headers['Cache-Control'] = 'no-store, private'
    return response


def _payload():
    if request.content_length and request.content_length > MAX_CONFIG_REQUEST_BYTES:
        raise ConfigTransferError('Configuration transfer exceeds the 10 MiB request limit', 413)
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        raise ConfigTransferError('Request body must be a JSON object')
    encoded = json.dumps(payload, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    if len(encoded) > MAX_CONFIG_REQUEST_BYTES:
        raise ConfigTransferError('Configuration transfer exceeds the 10 MiB request limit', 413)
    document = payload.get('document')
    scope = payload.get('scope')
    selected = payload.get('selectedFeeds')
    if not isinstance(document, dict):
        raise ConfigTransferError('document must be an object')
    return document, scope, selected


@api.route('/system/config-import/preview', methods=['POST'])
@limiter.limit('20 per hour')
@log_request
def preview_runtime_config_import():
    try:
        document, scope, selected = _payload()
        return json_response(build_preview(get_database(), document, scope, selected))
    except ConfigTransferError as exc:
        return error_response(str(exc), exc.status)
    except Exception:
        logger.exception('Runtime configuration preview failed')
        return error_response('Runtime configuration preview failed', 500)


@api.route('/system/config-import', methods=['POST'])
@limiter.limit('5 per hour')
@log_request
def apply_runtime_config_import():
    try:
        document, scope, selected = _payload()
        body = request.get_json()
        token = body.get('previewToken')
        if not isinstance(token, str):
            raise ConfigTransferError('previewToken is required')
        return json_response(apply_config(
            get_database(), document, scope, selected, token))
    except ConfigTransferError as exc:
        return error_response(str(exc), exc.status)
    except Exception:
        logger.exception('Runtime configuration import failed')
        return error_response('Runtime configuration import failed', 500)
