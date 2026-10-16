import fcntl
import ipaddress
import os
from pathlib import Path
import secrets
import sqlite3
import sys

repo = Path(__file__).resolve().parents[3]
data = Path(os.environ['MINUSPOD_AUDIT_DATA'])
port = int(os.environ.get('MINUSPOD_AUDIT_PORT', '8000'))
writable = os.environ.get('MINUSPOD_AUDIT_WRITABLE') == '1'
run_jobs = writable and os.environ.get('MINUSPOD_AUDIT_RUN_JOBS') == '1'
assert (data / 'podcast.db').is_file()
os.environ.update({
    'DATA_DIR': str(data),
    'MINUSPOD_BACKGROUND_THREADS': '0',
    'MINUSPOD_REQUIRE_AUTH': 'false',
    'SESSION_COOKIE_SECURE': 'false',
    'SECRET_KEY': secrets.token_hex(32),
    'LOG_LEVEL': 'WARNING',
    'BASE_URL': f'http://127.0.0.1:{port}',
})
with sqlite3.connect(data / 'podcast.db') as connection:
    connection.execute("UPDATE settings SET value='' WHERE key='app_password'")

# Keep this inspection process out of the production leader role.
if not run_jobs:
    leader_lock = (data / '.background_leader.lock').open('w')
    fcntl.flock(leader_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)

def local_network_only(event, args):
    if event == 'socket.connect':
        address = args[1]
        if isinstance(address, tuple):
            try:
                local = ipaddress.ip_address(address[0]).is_loopback
            except ValueError:
                local = address[0] == 'localhost'
            if not local:
                raise PermissionError('External connections disabled for the backup audit')

sys.addaudithook(local_network_only)
sys.path.insert(0, str(repo / 'src'))
from main_app import app
from api import limiter
from flask import request, jsonify

limiter.enabled = False

@app.before_request
def inspection_reads_only():
    if not writable and request.method not in ('GET', 'HEAD', 'OPTIONS'):
        return jsonify(error='Mutations disabled in the audit instance'), 403

app.run(host='127.0.0.1', port=port, debug=False, use_reloader=False, threaded=True)
