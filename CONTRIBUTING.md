# Contributing to theater-mode

Thank you for contributing to `theater-mode`! We welcome bug fixes, hardware quirk workarounds, effect enhancements, and documentation improvements.

This guide outlines our development workflow, coding standards, and commit message conventions.

---

## 1. Development Workflow

### Prerequisites

* **Python 3.12+** with `PyGObject` bindings (`python3-gobject` or `pygobject`)
* **Pillow** (`python3-pillow`) — optional at runtime, required for the artwork tests
* **A C compiler, `make`, and libwayland** (`wayland-devel` / `libwayland-dev`) to build the dimmer
* **KDE Plasma 6 on Wayland**
* **Ruff** and **ShellCheck**

On an atomic distribution, the repository's Distrobox manifest provides these dependencies without layering packages onto the host:

```bash
distrobox assemble create --file distrobox.ini
distrobox enter theater-mode-dev -- ./bin/check
```

The first command creates the environment once; subsequent checks only need the second. The container has its own name and is not exported into the host's `PATH`.

**After editing `distrobox.ini`, recreate the container** — an existing one is never modified in place, and `--replace` does not override the manifest's `replace=false`:

```bash
distrobox assemble rm --file distrobox.ini
distrobox assemble create --file distrobox.ini
```

Distrobox shares your `$HOME` and places `~/.local/bin` ahead of the container's own directories on `PATH`, so a tool you have installed on the host takes precedence over the copy the manifest provides. This is why `bin/check` opens by printing the path and version of every tool it resolved: when a result differs from CI, that block usually explains it.

The manifest could set `home=` to sidestep this, and deliberately does not. A separate `HOME` would send `./install.sh` to a home the host's systemd never reads, which costs the one workflow the container exists for on an atomic host: build the helper here, then install it into your real session.

For the same reason, `$HOME` inside the container **is** your real home. Never run `./install.sh` there without redirecting it, or it will write to your actual `~/.local` and touch the host's user service:

```bash
env -u XDG_DATA_HOME -u XDG_CONFIG_HOME -u XDG_BIN_HOME -u XDG_CACHE_HOME \
    HOME=/tmp/fake-home ./install.sh --no-service
```

### Local Setup & Live Testing

To test your changes against your active desktop session, reinstall and follow the logs. The
install restarts the daemon for you, and `make` is a no-op when the C sources have not changed:

```bash
./install.sh
journalctl --user -u theater-mode.service -f
```

For anything D-Bus-shaped, prefer a daemon on a private bus over reinstalling: the entrypoints
in `bin/` add `src/` to the path themselves when run from a checkout, so no install is needed.

### Running Tests & Sanity Checks

Before committing, run the fast repository check script:

```bash
./bin/check
```

This builds `theater-dimmer` with `-Werror`, checks its version and the generated configuration reference, runs ShellCheck and the unit tests, then checks Python linting and formatting with Ruff.

You can also install `bin/check` as a git pre-commit hook:

```bash
ln -sf ../../bin/check .git/hooks/pre-commit
```

### Developer Environment Variables (Dev Keys)

Dev keys are settable **only via environment variables** and are reserved for development, test fixtures, and debugging:

| Environment Variable | Description |
| --- | --- |
| `THEATER_DEV_CONFIG_OVERRIDE` | Path to a replacement user configuration file (replaces the user layer entirely for tests). |
| `THEATER_DEV_SYSTEM_CONFIG_OVERRIDE` | Path to a replacement system configuration file. |
| `THEATER_DEV_FORCE_ART_DIR` | Path to a custom Steam library cache directory for artwork testing. |
| `THEATER_DEV_VERBOSE` | Enable verbose debug logging in daemon. |

---

## 2. Code Standards

* **Modern Python**: Target Python 3.12+. Every Python file should begin with `from __future__ import annotations`.
* **Type Annotations**: Provide explicit type hints for all function signatures and class attributes.
* **Docstrings**: Follow PEP 257 format (`"""Single-line summary."""` or summary followed by blank line and detailed notes).
* **Formatting & Linting**: We use [Ruff](pyproject.toml) and ShellCheck. `bin/check` expects the development environment to provide both tools and never downloads dependencies itself.
* **Modularity**: Keep hardware I/O (DRM sysfs, the dimmer helper, D-Bus) isolated from state tracking and heuristics so logic remains unit-testable without a physical compositor.

---

## 3. Git Commit Guidelines

We follow a **Modern Markdown Git style**: concise, imperative subjects with a body focused on context, rationale, and architectural trade-offs, formatted to render cleanly across modern IDEs and GitHub.

### Structure

```text
[subsystem:] <Imperative summary under 50-72 chars>

[Optional body: explain the *why*, context, or compositor quirks]
[Write in natural, soft-wrapped paragraphs and markdown-formatted lists]
[Do not insert manual hard line breaks mid-sentence]

[Optional issue references: Fixes #123, Closes #456]
```

### Subject Line Rules

* **Use the imperative mood** (*"Add support for..."*, *"Fix race condition in..."*, *"Decompose monolithic script..."* — not *"Added"*, *"Fixes"*, or *"Refactoring"*).
* **Do not end with a period**.
* **Capitalization**:
  * With a subsystem prefix: use **lowercase** after the colon (`effects: avoid...`), unless starting with a proper noun or code identifier (`steam: detect AppID...`).
  * Without a prefix: **capitalize** the first word (`Initial import of theater-mode`).
* **Approved Subsystems**:
  * `core:` — Package structure, shared abstractions, main runtime loop
  * `daemon:` — Lifecycle state machine, debouncing, snapshot sync
  * `effects:` — The dimmer helper, artwork, and effect construction
  * `display:` — DRM connector and mode detection
  * `steam:` — AppID detection, process inspection, gamescope heuristics
  * `kwin:` — The KWin detection script (`theater-detect`)
  * `install:` — `install.sh`, systemd units, packaging
  * `tests:` — Unit tests, mocks, test fixtures
  * `tools:` — Developer tools, `bin/check`, Ruff linting/formatting configs
  * `docs:` — README, documentation, inline architectural guides

### Commit Body Guidelines

* **Small, self-explanatory changes** (typos, single bug fixes) only need a concise one-line subject.
* **Non-trivial changes** should include a body that explains:
  1. **The problem / motivation**: What behavior was broken, unhandled, or missing?
  2. **The "Why" over "What"**: The diff already shows *what* changed; the commit message explains *why* this specific architecture or approach was chosen.
  3. **Compositor & Hardware quirks**: Document Wayland protocol constraints, Plasma quirks, D-Bus type inference, or Linux sandbox namespaces.
* **Formatting**:
  * Write in **natural, soft-wrapped paragraphs** (separate paragraphs with a single blank line).
  * **Do not insert manual hard line breaks mid-sentence.** Let IDEs and GitHub wrap text responsively.
  * Use markdown bullet lists (`- item`) where each item is a single, continuous line without artificial line wraps.
  * Use backticks for symbols, commands, and file paths (e.g. `kscreen-doctor`, `/sys/class/drm`).

### Examples

#### Good

```text
effects: drive dimmer fades from the wall clock rather than frame counts

A compositor stops delivering frame callbacks for an output that has been switched off, which froze fades partway through and left a screen dark. Deriving progress from elapsed time lets an output that wakes up mid-transition land where it should have been.
```

```text
steam: detect AppID from gamescope command line arguments

Gamescope starts before Steam injects SteamGameId into the environment. Parse the AppId parameter from reaper arguments in `/proc/<pid>/cmdline`.
```

```text
docs: update drop-in service configuration example
```

#### Avoid

```text
fixed a bug                      # Too vague, no subsystem, past tense
Refactored the display code.     # Trailing period, past tense, no rationale
wip on wallpapers                # Never commit work-in-progress to main
effects: changes                 # Says nothing about intent
```

---

## 4. Submitting Pull Requests

1. Create a descriptive branch for your work: `git checkout -b feature/my-enhancement`.
2. Ensure `./bin/check` passes cleanly with no test failures or linter warnings.
3. Write clean commits conforming to the guidelines above.
4. Submit a Pull Request with a clear description of your changes and any testing performed.
