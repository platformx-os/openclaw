#!/usr/bin/env python3
"""Offline source/build-contract gate; never imports or starts the application."""
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import tomllib


def relative_file(root, value):
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or not path.parts:
        raise ValueError("contract file must be relative and inside the source checkout")
    resolved = (root / value).resolve()
    if not resolved.is_relative_to(root.resolve()) or not resolved.is_file():
        raise ValueError("contract file missing or outside checkout: " + value)
    return resolved


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def docker_instruction(text, name):
    lines = [line.strip() for line in text.splitlines() if line.startswith(name + " ")]
    if not lines:
        raise ValueError("upstream Dockerfile has no " + name)
    return json.loads(lines[-1][len(name):].strip())


def validate(contract, root):
    if type(contract.get("schema")) is not int or contract["schema"] != 1:
        raise ValueError("unsupported source-contract schema")
    for name in ("upstream_repo", "fork_repo"):
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", contract.get(name, "")):
            raise ValueError("invalid repository identity: " + name)
    if not re.fullmatch(r"[0-9a-f]{40}", contract.get("upstream_commit", "")):
        raise ValueError("upstream_commit must be an immutable full SHA")
    if not re.fullmatch(r"v[0-9]+(?:[.][0-9]+)+(?:[-+][A-Za-z0-9.-]+)?", contract.get("release", "")):
        raise ValueError("release must be an explicit version tag")
    build = contract["build"]
    if build["context"] != ".":
        raise ValueError("only the reviewed repository-root build context is supported")
    dockerfile = relative_file(root, build["dockerfile"])
    if digest(dockerfile) != build["dockerfile_sha256"]:
        raise ValueError("Dockerfile changed: review runtime/build compatibility and refresh the contract")
    runtime = contract["runtime"]
    text = dockerfile.read_text()
    if docker_instruction(text, "ENTRYPOINT") != runtime["entrypoint"]:
        raise ValueError("contract entrypoint differs from upstream Dockerfile")
    if docker_instruction(text, "CMD") != runtime["image_command"]:
        raise ValueError("contract image command differs from upstream Dockerfile")
    if not runtime["command"] or not all(isinstance(x, str) and x for x in runtime["command"]):
        raise ValueError("service command must be an explicit nonempty argument array")
    for value in [runtime["state_directory"], *runtime["auxiliary_state_directories"]]:
        if not PurePosixPath(value).is_absolute() or ".." in PurePosixPath(value).parts:
            raise ValueError("persistent state requires an absolute container path")
    license_file = relative_file(root, contract["license"]["path"])
    if digest(license_file) != contract["license"]["sha256"]:
        raise ValueError("upstream license changed: review before updating its hash")
    labels = contract["provenance"]["required_oci_labels"]
    if labels.get("org.opencontainers.image.source") != "https://github.com/" + contract["fork_repo"]:
        raise ValueError("OCI source must identify the fork being built")
    if labels.get("org.opencontainers.image.revision") != "${BUILD_SOURCE_COMMIT}":
        raise ValueError("OCI revision must use the actual integration commit being built")
    if labels.get("org.opencontainers.image.version") != contract["release"]:
        raise ValueError("OCI version and selected release differ")
    if contract["patches"]["legacy_imported"] is not False or contract["patches"]["runtime_patches"]:
        raise ValueError("this preparation admits no implicit legacy/runtime patches")
    if contract["preparation"] != {"actions_enabled": False, "build_performed": False, "deployment_performed": False}:
        raise ValueError("preparation contract cannot claim enabled workflows, a build or deployment")
    version = contract["version_validation"]
    metadata = relative_file(root, version["metadata_file"])
    warnings = []
    if version["mode"] == "package_json":
        if json.loads(metadata.read_text())["version"] != contract["release"].removeprefix("v"):
            raise ValueError("selected release differs from upstream package version")
    elif version["mode"] == "release_commit":
        actual = tomllib.loads(metadata.read_text())["project"]["version"]
        if actual != version["metadata_version"] or not version.get("warning"):
            raise ValueError("release-only version exception requires unchanged metadata and an explicit warning")
        warnings.append(version["warning"])
    else:
        raise ValueError("unsupported upstream version validation mode")
    return warnings


def check_git_baseline(contract, root):
    env = os.environ.copy()
    env.update(GIT_OPTIONAL_LOCKS="0", GIT_NO_LAZY_FETCH="1", GIT_TERMINAL_PROMPT="0")
    result = subprocess.run(["git", "-c", "core.fsmonitor=false", "-C", str(root),
                             "merge-base", "--is-ancestor", contract["upstream_commit"], "HEAD"],
                            env=env, capture_output=True, timeout=10)
    if result.returncode:
        raise ValueError("pinned upstream baseline is absent or not an ancestor; use a correctly pinned source checkout")


def main():
    root = Path(__file__).resolve().parents[1]
    contract = json.loads((root / ".platformx/source.json").read_text())
    for warning in validate(contract, root):
        print("WARNING:", warning)
    check_git_baseline(contract, root)
    print("PASS: source contract, Docker interfaces, license, version and baseline ancestry")
    print("Not checked: image build, repository Actions setting, credential provisioning or live runtime behavior.")


if __name__ == "__main__":
    try:
        main()
    except (KeyError, ValueError, OSError, subprocess.SubprocessError) as error:
        print("FAIL:", error, file=sys.stderr)
        raise SystemExit(1)
