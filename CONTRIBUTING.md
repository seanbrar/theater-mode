# Contributing to theater-mode

Thank you for contributing to `theater-mode`! We welcome bug fixes, hardware quirk workarounds, effect enhancements, and documentation improvements.

This guide outlines our development workflow, coding standards, and commit message conventions.

---

## 1. Development Workflow

### Prerequisites

* **Python 3.10+** with `PyGObject` bindings (`python3-gobject` or `pygobject`)
* **KDE Plasma 6 on Wayland** with `kscreen-doctor`
* **Ruff** (recommended for local linting and formatting)

### Local Setup & Live Testing

To test your changes live against your active desktop session without repeatedly copying files:

```bash
# Symlink repo files into ~/.local/bin and ~/.local/share
./install.sh --link

# Restart the user daemon to pick up changes
systemctl --user restart theater-mode.service

# Follow live daemon logs
journalctl --user -u theater-mode.service -f
```

### Running Tests & Sanity Checks

Before committing, run the fast repository check script:

```bash
./bin/check
```

This runs:
1. The full unit test suite via `unittest`.
2. Ruff linter (`ruff check .`) and code formatter check (`ruff format --check .`).

You can also install `bin/check` as a git pre-commit hook:

```bash
ln -sf ../../bin/check .git/hooks/pre-commit
```

---

## 2. Code Standards

* **Modern Python**: Target Python 3.10+. Every Python file should begin with `from __future__ import annotations`.
* **Type Annotations**: Provide explicit type hints for all function signatures and class attributes.
* **Docstrings**: Follow PEP 257 format (`"""Single-line summary."""` or summary followed by blank line and detailed notes).
* **Formatting & Linting**: We use [Ruff](pyproject.toml) configured for 100-character line lengths and sorted imports (`isort`). Run `ruff format .` and `ruff check --fix .` to format code automatically.
* **Modularity**: Keep hardware I/O (DRM connectors, `kscreen-doctor`, D-Bus) isolated from state tracking and heuristics so logic remains unit-testable without a physical compositor.

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
  * `effects:` — Brightness, wallpaper, composite effect pipelines
  * `display:` — DRM connector detection, kscreen-doctor, Plasma scripting
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
  3. **Compositor & Hardware quirks**: Document DDC/CI latency, Wayland/Plasma quirks, D-Bus type inference, or Linux sandbox namespaces.
* **Formatting**:
  * Write in **natural, soft-wrapped paragraphs** (separate paragraphs with a single blank line).
  * **Do not insert manual hard line breaks mid-sentence.** Let IDEs and GitHub wrap text responsively.
  * Use markdown bullet lists (`- item`) where each item is a single, continuous line without artificial line wraps.
  * Use backticks for symbols, commands, and file paths (e.g. `kscreen-doctor`, `/sys/class/drm`).

### Examples

#### Good

```text
effects: avoid DDC/CI write queueing during rapid focus switches

Monitor panels drop intermediate values when bombarded with brightness changes. Settle on a single write per transition so the internal panel ramp executes smoothly.
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
