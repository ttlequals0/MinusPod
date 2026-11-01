#!/usr/bin/env python3
"""Content hash of the security package patch inputs, plus pin checks for the app Dockerfiles."""
import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPO = "ttlequals0/minuspod-security-packages"
DOCKERFILES = ("Dockerfile", "Dockerfile.cpu")
IMAGE_RE = re.compile(r"^ARG SECURITY_PACKAGES_IMAGE=(\S+)$", re.MULTILINE)
HASH_RE = re.compile(r"^# patch-inputs-hash: (\S+)$", re.MULTILINE)
LABEL = "io.minuspod.inputs-hash"
ZERO_DIGEST = "sha256:" + "0" * 64


def input_files(repo_root):
    # Must match the paths: filter in .github/workflows/security-packages.yml.
    files = [
        p for d in (repo_root / "scripts").glob("*-security") for p in d.rglob("*")
        if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc"
    ]
    files.append(repo_root / "scripts" / "build_media_security_packages.py")
    files.append(repo_root / "Dockerfile.security-packages")
    return sorted(files, key=lambda p: p.relative_to(repo_root).as_posix())


def content_hash(repo_root=ROOT):
    repo_root = Path(repo_root)
    digest = hashlib.sha256()
    for path in input_files(repo_root):
        digest.update(path.relative_to(repo_root).as_posix().encode() + b"\0")
        digest.update(path.read_bytes())
    return digest.hexdigest()[:16]


def read_pin(repo_root, name):
    text = (Path(repo_root) / name).read_text()
    image = IMAGE_RE.search(text)
    stamp = HASH_RE.search(text)
    return (image.group(1) if image else None, stamp.group(1) if stamp else None)


def check(repo_root=ROOT, resolve=False):
    repo_root = Path(repo_root)
    errors = []
    computed = content_hash(repo_root)
    pins = {name: read_pin(repo_root, name) for name in DOCKERFILES}
    for name, (_, stamp) in pins.items():
        if stamp != computed:
            errors.append(
                f"{name} patch-inputs-hash {stamp} differs from computed {computed}: patch inputs changed. "
                "Run the security-packages workflow, then scripts/bump_security_packages.sh."
            )
    images = {image for image, _ in pins.values()}
    if len(images) != 1:
        errors.append(f"SECURITY_PACKAGES_IMAGE differs between {' and '.join(DOCKERFILES)}.")
        return errors
    image = images.pop()
    digest = image.partition("@")[2] if image else ""
    if digest == ZERO_DIGEST or not digest:
        errors.append("SECURITY_PACKAGES_IMAGE is the placeholder digest; run scripts/bump_security_packages.sh.")
    elif resolve:
        errors.extend(resolve_errors(computed, digest))
    return errors


def resolve_errors(computed, digest):
    try:
        result = subprocess.run(
            ["docker", "buildx", "imagetools", "inspect", f"{REPO}@{digest}", "--format", "{{json .Image}}"],
            capture_output=True, text=True,
        )
    except FileNotFoundError:
        return ["docker is not installed; --resolve needs docker buildx."]
    if result.returncode != 0:
        return [f"Pinned digest {digest} not found in {REPO}."]
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return [f"Could not parse the image config for {REPO}@{digest}."]
    configs = list(data.values()) if data and all("/" in k for k in data) else [data]
    labels = [((c or {}).get("config") or {}).get("Labels") or {} for c in configs]
    found = [lab[LABEL] for lab in labels if LABEL in lab]
    if computed in found:
        return []
    return [f"Pinned digest {digest} carries inputs hash {found[0] if found else 'none'}, expected {computed}."]


def pin(image_ref, repo_root=ROOT):
    repo_root = Path(repo_root)
    new_hash = content_hash(repo_root)
    changed = False
    for name in DOCKERFILES:
        path = repo_root / name
        text = path.read_text()
        old_image, old_hash = read_pin(repo_root, name)
        if (old_image, old_hash) != (image_ref, new_hash):
            changed = True
        print(f"{name} old: {old_image} {old_hash}")
        print(f"{name} new: {image_ref} {new_hash}")
        text = IMAGE_RE.sub(lambda _: f"ARG SECURITY_PACKAGES_IMAGE={image_ref}", text)
        text = HASH_RE.sub(lambda _: f"# patch-inputs-hash: {new_hash}", text)
        path.write_text(text)
    if not changed:
        print("already pinned")
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="verify the Dockerfile pins match the inputs")
    parser.add_argument("--resolve", action="store_true", help="with --check, also verify the pinned digest on the registry")
    parser.add_argument("--pin", metavar="IMAGE_REF", help="rewrite the pin and hash comment in both Dockerfiles")
    args = parser.parse_args()
    if args.pin:
        return pin(args.pin)
    if args.resolve and not args.check:
        parser.error("--resolve requires --check")
    if not args.check:
        print(content_hash())
        return 0
    errors = check(resolve=args.resolve)
    for error in errors:
        print(error, file=sys.stderr)
    if not errors:
        print("security packages pin is current")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
