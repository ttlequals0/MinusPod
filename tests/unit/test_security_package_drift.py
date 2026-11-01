import importlib.util
import json
import subprocess
from pathlib import Path
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "check_security_package_drift.py"
spec = importlib.util.spec_from_file_location("check_security_package_drift", SCRIPT)
drift = importlib.util.module_from_spec(spec)
spec.loader.exec_module(drift)

POLICY = """libahead:
  Installed: (none)
  Candidate: 0.9-1
  Version table:
libbehind:
  Installed: (none)
  Candidate: 2.0-1
libcurrent:
  Installed: (none)
  Candidate: 1.0-1
libunknown:
  Installed: (none)
  Candidate: (none)
"""
COMPARE = {("2.0-1", "1.0-1"): 0, ("0.9-1", "1.0-1"): 1}
PACKAGES = ["libahead", "libbehind", "libcurrent", "libunknown"]


def fake_run(policy=POLICY):
    def run(cmd, **kwargs):
        if cmd[0] == "apt-cache":
            return subprocess.CompletedProcess(cmd, 0, stdout=policy)
        _, _, cand, _, ours = cmd
        return subprocess.CompletedProcess(cmd, COMPARE[(cand, ours)])
    return run


def make_repo(root, packages=PACKAGES):
    d = root / "scripts" / "core-security"
    d.mkdir(parents=True)
    entries = [{"name": "pkg", "version": "1.0-1+minuspod1", "runtime_packages": packages}]
    (d / "manifest.json").write_text(json.dumps({"packages": entries}))
    return root


def test_status_per_runtime_package(tmp_path):
    with patch.object(drift.subprocess, "run", side_effect=fake_run()) as run:
        rows = drift.collect(make_repo(tmp_path))
    assert {r["package"]: r["status"] for r in rows} == {
        "libahead": "ahead", "libbehind": "behind", "libcurrent": "current", "libunknown": "unknown",
    }
    assert sum(c.args[0][0] == "apt-cache" for c in run.call_args_list) == 1
    assert rows[0]["pinned"] == "1.0-1"
    assert rows[0]["family"] == "core"


def test_markdown_summary(tmp_path):
    with patch.object(drift.subprocess, "run", side_effect=fake_run()):
        text = drift.markdown(drift.collect(make_repo(tmp_path)))
    assert "| core | pkg | libbehind | 1.0-1 | 2.0-1 | behind |" in text
    assert text.splitlines()[-1].startswith("1 behind, 1 current, 1 ahead (not covered by this check), 1 unknown of 4")


def exit_code(tmp_path, monkeypatch, packages, policy):
    repo = make_repo(tmp_path, packages)
    original = drift.collect
    monkeypatch.setattr(drift, "collect", lambda: original(repo))
    with patch.object(drift.subprocess, "run", side_effect=fake_run(policy)):
        return drift.main()


def test_exit_0_when_current(tmp_path, monkeypatch):
    assert exit_code(tmp_path, monkeypatch, ["libcurrent", "libahead"], POLICY) == 0


def test_exit_1_when_behind(tmp_path, monkeypatch):
    assert exit_code(tmp_path, monkeypatch, ["libcurrent", "libbehind"], POLICY) == 1


def test_exit_0_when_partly_unknown(tmp_path, monkeypatch):
    assert exit_code(tmp_path, monkeypatch, ["libcurrent", "libunknown"], POLICY) == 0


def test_exit_2_when_all_unknown(tmp_path, monkeypatch):
    assert exit_code(tmp_path, monkeypatch, ["libunknown"], POLICY) == 2


def test_exit_2_when_no_rows(tmp_path, monkeypatch):
    assert exit_code(tmp_path, monkeypatch, [], "") == 2
