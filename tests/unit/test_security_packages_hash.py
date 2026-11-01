import importlib.util
import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "security_packages_hash.py"
spec = importlib.util.spec_from_file_location("security_packages_hash", SCRIPT)
sph = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sph)

DIGEST = "sha256:" + "a" * 64
IMAGE = f"ttlequals0/minuspod-security-packages@{DIGEST}"


def make_repo(root, image=IMAGE, stamp=None, cpu_image=None):
    (root / "scripts" / "core-security").mkdir(parents=True)
    (root / "scripts" / "core-security" / "manifest.json").write_text('{"packages": []}')
    (root / "scripts" / "build_media_security_packages.py").write_text("# build\n")
    (root / "Dockerfile.security-packages").write_text("FROM scratch\n")
    stamp = stamp or sph.content_hash(root)
    for name, img in (("Dockerfile", image), ("Dockerfile.cpu", cpu_image or image)):
        (root / name).write_text(f"ARG SECURITY_PACKAGES_IMAGE={img}\n# patch-inputs-hash: {stamp}\n")
    return root


def fake_run(stdout="", returncode=0):
    return subprocess.CompletedProcess([], returncode, stdout=stdout, stderr="")


def config(hash_value):
    labels = {} if hash_value is None else {sph.LABEL: hash_value}
    return {"config": {"Labels": labels}}


def test_content_hash_stable_and_sensitive(tmp_path):
    repo = make_repo(tmp_path)
    first = sph.content_hash(repo)
    assert sph.content_hash(repo) == first
    (repo / "Dockerfile.security-packages").write_text("FROM scratch\nRUN true\n")
    assert sph.content_hash(repo) != first


def test_check_passes_for_matching_pin(tmp_path):
    assert sph.check(make_repo(tmp_path)) == []


def test_check_reports_placeholder_digest(tmp_path):
    repo = make_repo(tmp_path, image=f"ttlequals0/minuspod-security-packages@{sph.ZERO_DIGEST}")
    assert any("placeholder" in e for e in sph.check(repo))


def test_check_reports_hash_mismatch(tmp_path):
    errors = sph.check(make_repo(tmp_path, stamp="deadbeef"))
    assert len(errors) == 2
    assert all("differs from computed" in e for e in errors)


def test_check_reports_differing_args(tmp_path):
    repo = make_repo(tmp_path, cpu_image="ttlequals0/minuspod-security-packages@sha256:" + "b" * 64)
    assert any("differs between" in e for e in sph.check(repo))


@pytest.mark.parametrize("shape", ["single", "platform_map"])
def test_resolve_passes_on_matching_label(shape):
    payload = config("abc") if shape == "single" else {"linux/amd64": config("abc"), "linux/arm64": config("abc")}
    with patch.object(sph.subprocess, "run", return_value=fake_run(json.dumps(payload))) as run:
        assert sph.resolve_errors("abc", DIGEST) == []
    assert f"{sph.REPO}@{DIGEST}" in run.call_args.args[0]


@pytest.mark.parametrize("label,shown", [(None, "none"), ("old", "old")])
def test_resolve_reports_label_mismatch(label, shown):
    with patch.object(sph.subprocess, "run", return_value=fake_run(json.dumps(config(label)))):
        assert sph.resolve_errors("abc", DIGEST) == [
            f"Pinned digest {DIGEST} carries inputs hash {shown}, expected abc."
        ]


def test_resolve_reports_digest_not_found():
    with patch.object(sph.subprocess, "run", return_value=fake_run(returncode=1)):
        assert "not found" in sph.resolve_errors("abc", DIGEST)[0]
