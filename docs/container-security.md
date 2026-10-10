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

## Updating the packages

1. Check the installed Ubuntu package versions and official upstream advisories. Prefer a public Ubuntu security update when it contains the required fixes.
2. Update the pinned source and patch entries. Review each patch against the Ubuntu source, including patches already applied by Ubuntu. Preserve attribution when porting upstream lines into local patch files.
3. Build the affected security builder targets and review their logs. Package recipes run their configured tests; failures stop the image build. GLib and auxiliary packages build as an unprivileged user so permission tests exercise normal access rules. Glibc builds the native runtime with its full tests and omits unused secondary architectures.
4. Build both image variants. Run `scripts/check_image_runtime.py` against each final image as UID 1000. It requires every security provenance family, checks installed versions, exercises native PCRE2 JIT and libc conversion, and verifies media decoding, stream copy, metadata, Chromaprint, and Python imports. Check application startup and health separately. The CPU workflow runs these checks on native amd64 and arm64 runners before publishing the manifest.
5. Scan each completed image for vulnerabilities and secrets. Compare vendor findings with the applied source patches, and document residual findings before publication.

Do not remove package records or add vulnerability exclusions to hide findings. A distro scanner can continue reporting a CVE for a locally patched package until the vendor tracker recognizes a fixed version. The manifest records the source fix; the scanner result remains part of the release evidence. A source-only fix for a disabled FFmpeg component does not establish a reduction in runtime exposure.

Remaining vendor findings require separate assessment of the affected code, available fixes, and application exposure. A passing scan at a severity threshold does not mean every reported vulnerability has been fixed.
