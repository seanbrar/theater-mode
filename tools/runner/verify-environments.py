#!/usr/bin/env python3
"""Verify that local release images match the hosted release job contracts."""

from __future__ import annotations

import pathlib
import re
import shlex
import sys

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]


def packages(command: str) -> set[str]:
    """Return packages following apt's no-recommends option in a shell command."""
    tokens = shlex.split(command.replace("\\\n", " "))
    try:
        start = tokens.index("--no-install-recommends") + 1
    except ValueError as exc:
        raise ValueError("install command lacks --no-install-recommends") from exc
    candidates = tokens[start:]
    if "&&" in candidates:
        candidates = candidates[: candidates.index("&&")]
    result = {token for token in candidates if not token.startswith(">")}
    if not result:
        raise ValueError("install command contains no packages")
    return result


def install_step(job: dict[str, object]) -> str:
    """Return the apt installation command from a workflow job."""
    for step in job["steps"]:  # type: ignore[index]
        if isinstance(step, dict) and "Install " in str(step.get("name", "")):
            return str(step["run"])
    raise ValueError("workflow job has no dependency installation step")


def verify_profile(jobs: dict[str, object], job_name: str, containerfile: str) -> None:
    """Verify one workflow job against its local Containerfile."""
    job = jobs[job_name]
    if not isinstance(job, dict):
        raise ValueError(f"workflow job {job_name} is not a mapping")
    local = (ROOT / "tools/runner" / containerfile).read_text(encoding="utf-8")
    workflow_image = str(job["container"])
    local_image = local.splitlines()[0].removeprefix("FROM docker.io/library/")
    if workflow_image != local_image:
        raise ValueError(
            f"{job_name} images differ: workflow {workflow_image}, local {local_image}"
        )
    shell = job.get("defaults", {}).get("run", {}).get("shell")  # type: ignore[union-attr]
    if shell != "bash":
        raise ValueError(f"{job_name} steps do not explicitly run under bash")
    workflow_packages = packages(install_step(job))
    local_packages = packages(local)
    if workflow_packages != local_packages:
        raise ValueError(
            f"{job_name} packages differ: workflow-only {sorted(workflow_packages - local_packages)}, "
            f"local-only {sorted(local_packages - workflow_packages)}"
        )


def verify_native_profile() -> None:
    """Verify the native image covers the hosted job's declared environment."""
    workflow = yaml.safe_load((ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8"))
    job = workflow["jobs"]["native"]
    local = (ROOT / "tools/runner/Containerfile").read_text(encoding="utf-8")
    runner = str(job["runs-on"])
    local_image = next(
        line for line in local.splitlines() if line.startswith("FROM ")
    ).removeprefix("FROM docker.io/library/")
    if runner.removeprefix("ubuntu-").replace("-", ".") != local_image.removeprefix("ubuntu:"):
        raise ValueError(f"native images differ: workflow {runner}, local {local_image}")

    workflow_packages = packages(install_step(job))
    local_packages = packages(local)
    missing = workflow_packages - local_packages
    if missing:
        raise ValueError(f"native image lacks workflow packages: {sorted(missing)}")

    uv_step = next(
        step for step in job["steps"] if "astral-sh/setup-uv@" in str(step.get("uses", ""))
    )
    uv_version = str(uv_step["with"]["version"])
    if f"https://astral.sh/uv/{uv_version}/install.sh" not in local:
        raise ValueError(f"native image does not install workflow uv {uv_version}")
    workflow_ruff = re.search(r'ruff[^"\n]*', str(job["steps"][3]["run"]))
    local_ruff = re.search(r'ruff[^"\n]*', local)
    if workflow_ruff is None or local_ruff is None or workflow_ruff.group() != local_ruff.group():
        raise ValueError("native Ruff constraints differ")


def main() -> None:
    """Verify every locally simulated release environment."""
    workflow = yaml.safe_load((ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8"))
    jobs = workflow["jobs"]
    verify_profile(jobs, "build", "Containerfile.release-build")
    verify_profile(jobs, "verify", "Containerfile.release-runtime")
    verify_native_profile()
    print("[runner] Local environment definitions match the hosted jobs.")


if __name__ == "__main__":
    try:
        main()
    except (KeyError, TypeError, ValueError) as exc:
        sys.exit(f"release environment mismatch: {exc}")
