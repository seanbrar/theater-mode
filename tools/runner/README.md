# tools/runner — Ubuntu CI runner validation

Validates that the current checkout builds, lints, and passes the native CI install and release lifecycle under Ubuntu 24.04. This reproduces the job's GCC 13.3, ShellCheck 0.9.0, Python 3.12, and glibc 2.39 diagnostics without claiming coverage of the separate Python version matrix.

Use this before pushing or tagging to catch compiler range diagnostics, fortify warnings, and linter regressions locally.

---

## Quick run

```sh
# Run the complete native CI lifecycle
bin/check-ci

# Run only bin/check in the Ubuntu environment
bin/check-ci --quick

# Rehearse the Ubuntu 22.04 release build and Ubuntu 24.04 runtime gate
bin/check-ci --release

# Require the source version to match a proposed tag
bin/check-ci --release v0.1.0-alpha.2
```

---

## Prerequisites

Requires an x86_64 host with `podman` or `docker`. Each runner image is built automatically on its first use and whenever its `Containerfile` changes. Images remain cached until their definition changes or `--rebuild` explicitly refreshes the Ubuntu bases and packages.

To pull the current Ubuntu bases and rebuild every image:
```sh
bin/check-ci --rebuild
```

---

## Common recipes

```sh
# Run a specific non-interactive command
bin/check-ci -- ./bin/check-abi-floor src/theater_mode/dimmer/theater-dimmer

# Open an interactive shell inside the runner
bin/check-ci --shell
```

---

<details>
<summary>How runner isolation works</summary>

* **Read-only source:** The checkout is mounted read-only, including uncommitted and untracked files, then copied into a private workspace before any command runs.
* **RAM-backed builds:** The workspace, compilation, sanitizer tests, and intermediate files live on a private tmpfs mount in RAM (`/tmp`).
* **Cross-toolchain hygiene:** Generated helpers are discarded from the private copy before execution. Ubuntu artifacts cannot overwrite or remove builds in the working checkout.
* **Hermetic environment:** The containers do not inherit host environment variables or ambient Python packages in `$HOME/.local`.
* **Release boundary:** Release artifacts cross between a build image and a compiler-free runtime image through a private container volume. Source files never enter the runtime gate.
</details>
