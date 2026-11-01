#!/usr/bin/env python3
"""Report pinned security packages that Ubuntu has since passed. Run where apt lists are current."""
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SUFFIX = "+minuspod1"
STATUSES = ("behind", "current", "ahead", "unknown")


def candidates(packages):
    """Map each package to its apt candidate version, or None when unavailable."""
    out = subprocess.run(["apt-cache", "policy", *packages], capture_output=True, text=True).stdout
    result = {}
    for block in re.split(r"^(?=\S.*:$)", out, flags=re.MULTILINE):
        name, _, body = block.partition("\n")
        match = re.search(r"^\s*Candidate: (\S+)", body, re.MULTILINE)
        if name.endswith(":") and match and match.group(1) != "(none)":
            result[name[:-1]] = match.group(1)
    return {p: result.get(p) for p in packages}


def compare(cand, ours):
    if cand == ours:
        return "current"
    newer = subprocess.run(["dpkg", "--compare-versions", cand, "gt", ours]).returncode == 0
    return "behind" if newer else "ahead"


def collect(root=ROOT):
    entries = []
    for manifest in sorted(Path(root).glob("scripts/*-security/manifest.json")):
        family = manifest.parent.name.removesuffix("-security")
        for entry in json.loads(manifest.read_text())["packages"]:
            for pkg in entry.get("runtime_packages", []):
                entries.append((family, entry["name"], pkg, entry["version"].removesuffix(SUFFIX)))
    cands = candidates(sorted({e[2] for e in entries})) if entries else {}
    rows = []
    for family, source, pkg, ours in entries:
        cand = cands[pkg]
        status = "unknown" if cand is None else compare(cand, ours)
        rows.append({"family": family, "source": source, "package": pkg,
                     "pinned": ours, "candidate": cand or "-", "status": status})
    return rows


def markdown(rows):
    lines = ["| Family | Source package | Runtime package | Pinned version | Ubuntu candidate | Status |",
             "|---|---|---|---|---|---|"]
    lines += [f"| {r['family']} | {r['source']} | {r['package']} | {r['pinned']} | {r['candidate']} | {r['status']} |"
              for r in rows]
    counts = {s: sum(r["status"] == s for r in rows) for s in STATUSES}
    lines += ["", f"{counts['behind']} behind, {counts['current']} current, {counts['ahead']} ahead "
              f"(not covered by this check), {counts['unknown']} unknown of {len(rows)} runtime packages."]
    return "\n".join(lines)


def main():
    rows = collect()
    print(markdown(rows))
    if not rows or all(r["status"] == "unknown" for r in rows):
        return 2
    return 1 if any(r["status"] == "behind" for r in rows) else 0


if __name__ == "__main__":
    sys.exit(main())
