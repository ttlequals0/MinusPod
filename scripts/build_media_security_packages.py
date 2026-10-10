#!/usr/bin/env python3
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import urllib.request
from pathlib import Path


INPUTS = Path(__file__).resolve().parent / 'media-security'
WORK = Path('/work/media-security')
OUTPUT = Path('/out')


def run(command, cwd=None, log=None, env=None):
    print(' '.join(map(str, command)), flush=True)
    if log is None:
        subprocess.run(command, cwd=cwd, env=env, check=True)
        return
    with log.open('w') as handle:
        result = subprocess.run(command, cwd=cwd, env=env, stdout=handle, stderr=subprocess.STDOUT)
    if result.returncode:
        print('\n'.join(log.read_text().splitlines()[-80:]), flush=True)
        result.check_returncode()


def fetch(entry, directory):
    if 'path' in entry:
        data = (INPUTS / entry['path']).read_bytes()
    else:
        with urllib.request.urlopen(entry['url'], timeout=60) as response:
            data = response.read()
    if hashlib.sha256(data).hexdigest() != entry['sha256']:
        raise ValueError(f"Checksum mismatch: {entry['file']}")
    target = directory / entry['file']
    target.write_bytes(data)
    return target


def main(prepare_only=False):
    manifest = json.loads((INPUTS / 'manifest.json').read_text())
    provenance = OUTPUT / 'provenance'
    for directory in (WORK, OUTPUT / 'packages', provenance / 'sources', provenance / 'logs'):
        directory.mkdir(parents=True, exist_ok=True)
    shutil.copy2(INPUTS / 'manifest.json', provenance / 'manifest.json')
    shutil.copy2(Path(__file__), provenance / 'build_media_security_packages.py')
    env = os.environ | {
        'QUILT_PATCHES': 'debian/patches',
        'DEBFULLNAME': 'MinusPod contributors',
        'DEBEMAIL': 'noreply@users.noreply.github.com',
        'SOURCE_DATE_EPOCH': str(manifest['source_date_epoch']),
    }
    packages = []
    for package in manifest['packages']:
        print(f"Building {package['name']} {package['version']}", flush=True)
        parent = WORK / package['name']
        files = parent / 'inputs'
        files.mkdir(parents=True)
        downloaded = {entry['file']: fetch(entry, files) for entry in package['source_files']}
        source = parent / 'source'
        if package['name'] == 'srt':
            source.mkdir()
            run(['tar', '-xf', downloaded[package['upstream_archive']], '--strip-components=1', '-C', source])
            run(['tar', '-xf', downloaded[package['debian_archive']], '-C', source])
            cmake = (source / 'CMakeLists.txt').read_text()
            if 'cmake_minimum_required (VERSION 3.5 FATAL_ERROR)' not in cmake:
                raise ValueError('SRT CMake compatibility fix is missing')
            series = source / 'debian/patches/series'
            series.write_text(series.read_text().replace('FTBFS_with_CMake_4.patch\n', ''))
            rules = source / 'debian/rules'
            rules.write_text(rules.read_text().replace('ENABLE_UINTTESTS', 'ENABLE_UNITTESTS'))
            run(['quilt', 'push', '-a'], cwd=source, env=env)
        else:
            run(['dpkg-source', '--no-check', '-x', downloaded[package['dsc']], source])
        for entry in package['patches']:
            patch = fetch(entry, files)
            run(['quilt', 'import', '-P', entry['file'], patch], cwd=source, env=env)
            run(['quilt', 'push'], cwd=source, env=env)

        run(['dch', '--newversion', package['version'], '--distribution', 'resolute', package['summary']], cwd=source, env=env)
        run(['tar', '-cJf', provenance / 'sources' / f"{package['name']}.tar.xz", '-C', source, '.'])
        if prepare_only:
            continue
        run(['dpkg-buildpackage', '-B', '-uc', '-us', '-jauto'], cwd=source, env=env,
            log=provenance / 'logs' / f"{package['name']}.log")
        expected = set(package['runtime_packages'])
        found = set()
        for archive in parent.glob('*.deb'):
            name, version, arch = subprocess.check_output(
                ['dpkg-deb', '--show', '--showformat=${Package}\n${Version}\n${Architecture}', archive], text=True,
            ).splitlines()
            if name not in expected:
                continue
            found.add(name)
            shutil.copy2(archive, OUTPUT / 'packages' / archive.name)
            packages.append({'name': name, 'version': version, 'architecture': arch,
                             'sha256': hashlib.sha256(archive.read_bytes()).hexdigest()})
        if found != expected:
            raise ValueError(f"Missing runtime packages: {sorted(expected - found)}")
    if not prepare_only:
        (provenance / 'built-packages.json').write_text(json.dumps(packages, indent=2) + '\n')


def verify_installed():
    packages = json.loads((Path(__file__).parent / 'built-packages.json').read_text())
    architecture = subprocess.check_output(['dpkg', '--print-architecture'], text=True).strip()
    for package in packages:
        version, arch = subprocess.check_output(
            ['dpkg-query', '--show', '--showformat=${Version}\n${Architecture}', package['name']], text=True,
        ).splitlines()
        if (version, arch) != (package['version'], package['architecture']) or arch != architecture:
            raise ValueError(f"Installed media package differs from build: {package['name']}")
    print(f'Verified {len(packages)} media packages for {architecture}', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--verify-installed', action='store_true')
    mode.add_argument('--prepare-only', action='store_true')
    options = parser.parse_args()
    if options.verify_installed:
        verify_installed()
    else:
        main(prepare_only=options.prepare_only)
