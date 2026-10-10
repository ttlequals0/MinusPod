# Container media security updates

GPU and CPU images build the same patched media packages on Ubuntu 26.04. The build keeps Ubuntu's package recipes, enabled codecs, and shared-library package family. Build tools stay in a separate stage.

`scripts/media-security/manifest.json` pins each source archive and patch by URL and SHA-256. FFmpeg, libass, and libsndfile use Ubuntu source packages with selected parser and filter backports. SRT uses the upstream 1.5.6 security release with Ubuntu's packaging. Local patches adapt upstream fixes to these package sources. IRCAM sample-rate validation prevents invalid floating-point conversions; it is a mitigation, not a complete fix for CVE-2025-52194.

The runtime image contains the manifest, package checksums, build script, build logs, and complete patched source trees under `/usr/share/minuspod/media-security/`. The source trees include Ubuntu packaging, applied patches, and license files. Installed packages retain their copyright notices. Package versions have a `+minuspod1` suffix to identify local builds.

## Updating the packages

1. Check the installed Ubuntu package versions and official upstream advisories. Prefer a public Ubuntu security update when it contains the required fixes.
2. Update the pinned source and patch entries. Review each patch against the Ubuntu source, including patches already applied by Ubuntu. Preserve attribution when porting upstream lines into local patch files.
3. Build the `media-security-builder` target and review the package build logs. The Ubuntu recipes run their configured tests; test failures stop the image build.
4. Build both image variants. Verify media decoding, MP3 stream copy, metadata preservation, Chromaprint, Python media-library imports, and application startup. CPU images need native amd64 and arm64 checks.
5. Scan each completed image for vulnerabilities and secrets. Compare vendor findings with the applied source patches, and document residual findings before publication.

Do not remove package records or add vulnerability exclusions to hide findings. A distro scanner can continue reporting a CVE for a locally patched package until the vendor tracker recognizes a fixed version. The manifest records the source fix; the scanner result remains part of the release evidence. A source-only fix for a disabled FFmpeg component does not establish a reduction in runtime exposure.

These media updates do not replace core OS libraries. Remaining vendor findings require separate assessment of the affected code, available fixes, and application exposure. A passing scan at a severity threshold does not mean every reported vulnerability has been fixed.
