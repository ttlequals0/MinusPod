# Container security updates

GPU and CPU images build the same patched libraries and utilities on Ubuntu 26.04. Separate builder stages keep build tools out of the runtime image and let package families rebuild independently. Media updates retain the enabled codecs and matching shared-library package family.

`scripts/media-security/manifest.json` pins each source archive and patch by URL and SHA-256. FFmpeg, libass, and libsndfile use Ubuntu source packages with selected parser and filter backports. SRT uses the upstream 1.5.6 security release with Ubuntu's packaging. Local patches adapt upstream fixes to these package sources. IRCAM sample-rate validation prevents invalid floating-point conversions; it is a mitigation, not a complete fix for CVE-2025-52194.

Additional manifests under `scripts/core-security/`, `scripts/misc-security/`, `scripts/aux-security/`, and `scripts/graphics-security/` pin their sources and patches the same way:

| Family | Update |
| --- | --- |
| glibc | Backport the fix for empty `fopen` character sets and its upstream regression test. Keep the runtime library, utilities, and conversion modules at the same version. |
| PCRE2 | Build upstream 10.49 with Ubuntu's package recipe and 8-, 16-, and 32-bit tests. This includes newer arm64 JIT fixes and Unicode 17 data; Unicode matching can differ from 10.46. |
| GLib and cJSON | Backport destination-replacement symlink protection and escaped JSON Pointer decoding. GLib's library and architecture-independent data package ship together. |
| p11-kit, tar, bubblewrap, and libXrender | Apply the pinned security backports or upstream release recorded in the auxiliary manifest. Bubblewrap's source recipe comes from the official Debian archive. |
| Cairo and x264 | Backport glyph-buffer overflow checks and encoder-open failure cleanup. The Cairo change does not fix the older Cairo CVEs still reported by the vendor tracker. |

The runtime image contains each family's manifest, package checksums, build script, build logs, and complete patched source trees under `/usr/share/minuspod/<family>-security/`. Source trees include the package recipe, applied patches, and license files. Installed packages retain their copyright notices. Package versions have a `+minuspod1` suffix to identify local builds. Archive and patch SHA-256 values are verified; this does not authenticate source signatures.

## Published security packages image

CI builds `Dockerfile.security-packages` from the same manifests into `ttlequals0/minuspod-security-packages` for amd64 and arm64. The image is tagged by the content hash of the patch inputs.

To change the packages, open or update a pull request that changes any patch input. The security-packages workflow runs on that PR and publishes `ttlequals0/minuspod-security-packages:<hash>`. Fork PRs skip publishing.

Then run `scripts/bump_security_packages.sh`, which pins the manifest digest in both Dockerfiles, and commit. CI checks the hash comment on every pull request, and the release check verifies that the pinned digest carries the matching hash label.

The hash is a change detector for the patch inputs. The digest is the pin, because the Ubuntu base and build dependencies are not frozen by the hash.

Patched packages no longer rebuild on every release. To rebuild on demand, run the workflow with the `force` input and re-pin.

### Keeping the packages current

A weekly drift check compares each pinned runtime package with the Ubuntu 26.04 candidate. It opens the issue "Security packages behind Ubuntu" while any package is behind and closes it once all are current.

The check compares against Ubuntu's candidate only. Packages pinned ahead of Ubuntu, listed as ahead in the report, are not covered.

A package apt cannot resolve is listed as unknown, and the check fails only when nothing resolves.

A monthly refresh run rebuilds the image with fresh build dependencies but keeps the pinned source versions. It opens a pull request that re-pins the new digest, and it does not clear a drift issue, which needs a manifest update.

The bot pull request gets CI through an explicit dispatch and needs the repository setting that lets Actions open pull requests. CodeQL does not run on it; the change is two pin lines that CodeQL does not analyze, and CodeQL runs on main after the merge.

Each image also gets a dated tag, `<hash>-<YYYYMMDD>`. It keeps every pinned digest pullable after a rebuild moves the hash tag.

To build locally, run these commands. For the CPU image, add `-f Dockerfile.cpu` to the second command.

```
docker build -f Dockerfile.security-packages -t minuspod-security-packages:local .
docker build --build-arg SECURITY_PACKAGES_IMAGE=minuspod-security-packages:local .
```

The runtime image still copies each family's provenance and runs the installed-version check, so the evidence chain is unchanged.

## yt-dlp and Deno

Both images install a pinned yt-dlp and a pinned Deno for [YouTube feeds](youtube-feeds.md). The build downloads each release asset and verifies its SHA-256 checksum before installing it to `/usr/local/bin`; a mismatch fails the build. yt-dlp changes often to follow YouTube, so MinusPod runs a copy on the data volume that updates daily, outside the image, instead of waiting for a rebuild. The image copy is the floor: a newer image replaces an older volume copy, and MinusPod falls back to the image copy if the volume copy cannot execute. Turn **Update yt-dlp daily** off to freeze the volume copy. The cookies file lives on the data volume with mode 0600, is read only by yt-dlp, and is never logged.

## Updating the packages

1. Check the installed Ubuntu package versions and official upstream advisories. Prefer a public Ubuntu security update when it contains the required fixes.
2. Update the pinned source and patch entries. Review each patch against the Ubuntu source, including patches already applied by Ubuntu. Preserve attribution when porting upstream lines into local patch files.
3. Let the security-packages workflow run on the PR and review the logs under the provenance directories. Package recipes run their configured tests; failures stop the build. GLib and auxiliary packages build as an unprivileged user so permission tests exercise normal access rules. Glibc builds the native runtime with its full tests and omits unused secondary architectures. Then run `scripts/bump_security_packages.sh` and commit the Dockerfile pin changes.
4. Build both image variants. Run `scripts/check_image_runtime.py` against each final image as UID 1000. It requires every security provenance family, checks installed versions, exercises native PCRE2 JIT and libc conversion, and verifies media decoding, stream copy, metadata, Chromaprint, and Python imports. Check application startup and health separately. The CPU workflow runs these checks on native amd64 and arm64 runners before publishing the manifest.
5. Scan each completed image for vulnerabilities and secrets. Compare vendor findings with the applied source patches, and document residual findings before publication.

Do not remove package records or add vulnerability exclusions to hide findings. A distro scanner can continue reporting a CVE for a locally patched package until the vendor tracker recognizes a fixed version. The manifest records the source fix; the scanner result remains part of the release evidence. A source-only fix for a disabled FFmpeg component does not establish a reduction in runtime exposure.

Remaining vendor findings require separate assessment of the affected code, available fixes, and application exposure. A passing scan at a severity threshold does not mean every reported vulnerability has been fixed.

## Creating unsecured builds

Developers may build runtime images without the patched packages by using the `SECURITY_SRC=stub` build argument, which skips the published packages image. This significantly speeds up local development builds but results in images that contain the stock Ubuntu CVEs and **must not be published**. The per-family provenance directories under `/usr/share/minuspod/<family>-security/` will be empty in these unsecured builds.
