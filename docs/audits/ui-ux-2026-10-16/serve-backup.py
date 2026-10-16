import fcntl
import ipaddress
import os
from pathlib import Path
import secrets
import sqlite3
import sys

repo = Path(__file__).resolve().parents[3]
data = Path(os.environ['MINUSPOD_AUDIT_DATA'])
assert (data / 'podcast.db').is_file()
os.environ.update({
    'DATA_DIR': str(data),
    'MINUSPOD_BACKGROUND_THREADS': '0',
    'MINUSPOD_REQUIRE_AUTH': 'false',
    'SESSION_COOKIE_SECURE': 'false',
    'SECRET_KEY': secrets.token_hex(32),
    'LOG_LEVEL': 'WARNING',
    'BASE_URL': 'http://127.0.0.1:8000',
})
with sqlite3.connect(data / 'podcast.db') as connection:
    connection.execute("UPDATE settings SET value='' WHERE key='app_password'")

# Keep this inspection process out of the production leader role.
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
    if request.method not in ('GET', 'HEAD', 'OPTIONS'):
        return jsonify(error='Mutations disabled in the audit instance'), 403

app.run(host='127.0.0.1', port=8000, debug=False, use_reloader=False, threaded=True)
