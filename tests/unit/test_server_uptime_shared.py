"""Uptime is one value per server run, not one per gunicorn worker: a worker-local
stamp flipped the Settings page between two uptimes when a worker respawned after
an OOM kill. The shared status file holds one stamp per run, keyed on the owner."""
import os
import tempfile

os.environ.setdefault('MINUSPOD_DATA_DIR',
                      tempfile.mkdtemp(prefix='server-uptime-test-'))

from tests.app_bootstrap import bootstrap  # noqa: E402

_test_data_dir = bootstrap('server_uptime_test_')

from status_service import StatusService  # noqa: E402


def _svc():
    return StatusService()


def test_respawned_worker_keeps_the_first_stamp():
    svc = _svc()
    first = svc.claim_server_start_time(1000.0, owner='7')
    # The respawn imports the module later but shares the gunicorn master.
    second = svc.claim_server_start_time(5000.0, owner='7')
    assert first == 1000.0
    assert second == 1000.0
    assert svc.get_server_start_time() == 1000.0


def test_new_server_run_resets_the_stamp():
    svc = _svc()
    svc.claim_server_start_time(1000.0, owner='7')
    # A new container: different master pid, so uptime must reset.
    assert svc.claim_server_start_time(5000.0, owner='9') == 5000.0
    assert svc.get_server_start_time() == 5000.0


def test_every_worker_of_one_run_reports_the_same_value():
    svc = _svc()
    stamps = [svc.claim_server_start_time(t, owner='7')
              for t in (1000.0, 1000.4, 1001.2)]
    assert stamps == [1000.0, 1000.0, 1000.0]


def test_stamp_earlier_than_stored_replaces_it():
    """A clock that jumped backwards should not leave uptime running ahead."""
    svc = _svc()
    svc.claim_server_start_time(5000.0, owner='7')
    assert svc.claim_server_start_time(1000.0, owner='7') == 1000.0


def test_missing_stamp_reads_as_none():
    svc = _svc()
    if os.path.exists(svc.status_file):
        os.remove(svc.status_file)
    assert svc.get_server_start_time() is None
