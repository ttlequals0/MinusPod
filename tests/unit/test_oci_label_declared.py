"""Test that OCI image version labels are declared in Dockerfiles and wired in CI."""
import os
import re


def test_dockerfile_gpu_has_oci_label():
    """Verify GPU Dockerfile has ARG and LABEL for OCI version after final FROM."""
    dockerfile_path = os.path.join(
        os.path.dirname(__file__), "..", "..", "Dockerfile"
    )
    with open(dockerfile_path, "r") as f:
        content = f.read()

    # Find the final FROM statement
    from_matches = list(re.finditer(r"^FROM ", content, re.MULTILINE))
    assert len(from_matches) >= 1, "No FROM statements found"

    # Get content after the final FROM
    final_from_pos = from_matches[-1].start()
    after_final_from = content[final_from_pos:]

    # Check for ARG MINUSPOD_VERSION=dev after the final FROM
    assert re.search(
        r"^ARG MINUSPOD_VERSION=dev$", after_final_from, re.MULTILINE
    ), "ARG MINUSPOD_VERSION=dev not found after final FROM in Dockerfile"

    # Check for LABEL org.opencontainers.image.version="${MINUSPOD_VERSION}"
    assert re.search(
        r'^LABEL org\.opencontainers\.image\.version="\$\{MINUSPOD_VERSION\}"$',
        after_final_from,
        re.MULTILINE,
    ), "LABEL org.opencontainers.image.version not found after final FROM in Dockerfile"


def test_dockerfile_cpu_has_oci_label():
    """Verify CPU Dockerfile has ARG and LABEL for OCI version after final FROM."""
    dockerfile_path = os.path.join(
        os.path.dirname(__file__), "..", "..", "Dockerfile.cpu"
    )
    with open(dockerfile_path, "r") as f:
        content = f.read()

    # Find the final FROM statement
    from_matches = list(re.finditer(r"^FROM ", content, re.MULTILINE))
    assert len(from_matches) >= 1, "No FROM statements found"

    # Get content after the final FROM
    final_from_pos = from_matches[-1].start()
    after_final_from = content[final_from_pos:]

    # Check for ARG MINUSPOD_VERSION=dev after the final FROM
    assert re.search(
        r"^ARG MINUSPOD_VERSION=dev$", after_final_from, re.MULTILINE
    ), "ARG MINUSPOD_VERSION=dev not found after final FROM in Dockerfile.cpu"

    # Check for LABEL org.opencontainers.image.version="${MINUSPOD_VERSION}"
    assert re.search(
        r'^LABEL org\.opencontainers\.image\.version="\$\{MINUSPOD_VERSION\}"$',
        after_final_from,
        re.MULTILINE,
    ), "LABEL org.opencontainers.image.version not found after final FROM in Dockerfile.cpu"


def test_cpu_image_workflow_passes_minuspod_version():
    """Verify cpu-image.yml passes MINUSPOD_VERSION build-arg on both arch legs."""
    workflow_path = os.path.join(
        os.path.dirname(__file__),
        "..",
        "..",
        ".github",
        "workflows",
        "cpu-image.yml",
    )
    with open(workflow_path, "r") as f:
        content = f.read()

    # Look for build-args with MINUSPOD_VERSION in the Build and push step
    build_args_pattern = r"build-args:\s*MINUSPOD_VERSION=\$\{\{\s*inputs\.version\s*\}\}"
    matches = list(re.finditer(build_args_pattern, content))

    assert len(matches) >= 1, (
        "build-args: MINUSPOD_VERSION=${{ inputs.version }} not found in "
        "cpu-image.yml build-push step(s)"
    )
