import os
import stat

import pytest

from youtube import cookies


class FakeStorage:
    def __init__(self, tmp_path):
        self.data_dir = tmp_path


NETSCAPE = "# Netscape HTTP Cookie File\n.youtube.com\tTRUE\t/\tTRUE\t1800000000\tPREF\tf6=1\n"


def test_save_writes_0600_and_reports_status(tmp_path):
    storage = FakeStorage(tmp_path)
    status = cookies.save(storage, NETSCAPE)
    path = cookies.path(storage)
    assert status['set'] is True and status['lines'] == 1
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert cookies.status(storage)['set'] is True
    cookies.clear(storage)
    assert cookies.status(storage) == {'set': False, 'lines': 0, 'updatedAt': None}


def test_save_rejects_non_netscape(tmp_path):
    with pytest.raises(cookies.InvalidCookiesError):
        cookies.save(FakeStorage(tmp_path), 'PREF=f6=1; path=/')


def test_httponly_row_counts_as_cookie(tmp_path):
    row = "#HttpOnly_.youtube.com\tTRUE\t/\tTRUE\t1800000000\tSID\tabc"
    assert cookies.save(FakeStorage(tmp_path), row)['lines'] == 1


def test_header_plus_garbage_rejected(tmp_path):
    with pytest.raises(cookies.InvalidCookiesError):
        cookies.save(FakeStorage(tmp_path), "# Netscape HTTP Cookie File\nnot a cookie row\n")


def test_trailing_tab_on_empty_value_kept(tmp_path):
    row = ".youtube.com\tTRUE\t/\tTRUE\t1800000000\tPREF\t"
    assert cookies.save(FakeStorage(tmp_path), row)['lines'] == 1
    assert cookies.path(FakeStorage(tmp_path)).read_text().endswith('PREF\t\n')
