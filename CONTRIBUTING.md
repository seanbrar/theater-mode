# Contributing to theater-mode

Bug fixes, hardware quirk workarounds, effect enhancements, tests, and documentation
improvements are welcome. This guide describes the workflow enforced by CI.

---

## 1. Get a working development environment

### Prerequisites

`./bin/check` needs the Direct Runtime, Build Only, and Check Only tiers inventoried in
[section 4](#4-dependency-tiers--provenance). KDE Plasma is required only for live desktop
testing, not for the automated checks.

After cloning the repository, choose either the container setup or a native toolchain.

### Distrobox setup

On an atomic distribution, the repository's Distrobox manifest provides these dependencies without layering packages onto the host:

```bash
distrobox assemble create --file distrobox.ini
distrobox enter theater-mode-dev -- ./bin/check
```

The first command creates the environment once. Run the second from the repository for
every complete check. The container is named `theater-mode-dev`; its tools are not
exported into the host's `PATH`.

A separate container manifest at `tools/vm/distrobox.ini` defines `theater-mode-vm`,
used for optional pre-release Arch VM testing (see `tools/vm/README.md`).

The container shares your real home directory. This is useful for live testing, but it
also means that running `./install.sh` inside it installs into your real `~/.local` and can
restart your host's user service. For an isolated installer test, redirect `HOME`:

```bash
fake_home=$(mktemp -d)
env -u XDG_DATA_HOME -u XDG_CONFIG_HOME -u XDG_BIN_HOME -u XDG_CACHE_HOME \
    HOME="$fake_home" ./install.sh --no-service
env -u XDG_DATA_HOME -u XDG_CONFIG_HOME -u XDG_BIN_HOME -u XDG_CACHE_HOME \
    HOME="$fake_home" "$fake_home/.local/share/theater-mode/install.sh" \
    --uninstall --no-service --yes
```

If you change `distrobox.ini`, recreate the container; Distrobox does not apply manifest
changes to an existing container, and `--replace` does not override the manifest's
`replace=false`:

```bash
distrobox assemble rm --file distrobox.ini
distrobox assemble create --file distrobox.ini
```

Distrobox puts `~/.local/bin` early in `PATH`, so a host-installed tool can shadow the
container's copy. `bin/check` prints the resolved path and version of every tool; compare
that first block with CI when results differ.

### Native setup

Install the prerequisites listed above with your distribution's package manager. Common
package names include `python3-gobject`/`python-gobject`, `python3-yaml`/`python-yaml`,
`libwayland-dev`/`wayland-devel`, `binutils`, `nodejs`, `shellcheck`, and `ruff`. Then run:

```bash
./bin/check
```

## 2. Make and test a change

Run focused unit tests while iterating; they do not require Plasma or a running daemon:

```bash
python3 -m unittest tests.test_config -v
python3 -m unittest discover -s tests -t . -v
```

Before committing, run the same repository-wide entry point used by CI:

```bash
./bin/check
```

It builds `theater-dimmer` and `theater-art` with warnings treated as errors, verifies
their version and ABI floor, runs ASan/UBSan unit tests and oracle verification, checks
the generated configuration reference and KWin JavaScript, runs ShellCheck and all unit
tests, then checks Python linting and formatting with Ruff.

### Live Plasma testing

Live testing changes your current user installation. On a mutable host with the native
toolchain installed, reinstall directly from the checkout:

```bash
./install.sh
```

On an atomic host, do not run that command first: the checkout has no prebuilt helpers and
the host intentionally lacks the compiler toolchain. Build in Distrobox, then activate
the result from a host terminal:

```bash
distrobox enter theater-mode-dev -- sh -c 'make -C src/theater_mode/dimmer && make -C src/theater_mode/art'
./install.sh --dimmer-bin=src/theater_mode/dimmer/theater-dimmer --art-bin=src/theater_mode/art/theater-art
```

The second command must run on the host. Although the container shares `~/.local`, it does
not have the host's KDE configuration tools or session bus and therefore cannot reliably
enable the KWin script or restart the host service. Running `./bin/check` in Distrobox also
builds and verifies both helpers, so it can replace the first command.

For either path, verify the daemon and follow its logs:

```bash
theater-mode status
journalctl --user -u theater-mode.service -f
```

### Testing and verification ladder

Testing proceeds in tiers, from fast unit tests to full system verification:

1. **Unit and static checks** (fast iteration, ~1-15s):
   ```bash
   python3 -m unittest discover -s tests -t . -q
   ./bin/check   # Authoritative check: builds, ABI floor, ASan/UBSan, linters, tests
   ```

2. **Integration verification in a nested compositor** (~15s, no persistent host changes):
   ```bash
   # Assert clean effect activation across two synthetic displays
   tools/nested/nested-session.sh --check

   # Test multi-display topologies with corrupted or missing EDID
   tools/nested/nested-session.sh --check --profile triple-mixed
   ```
   See `tools/nested/README.md` for interactive modes, headless CI checks, and profile options.

3. **Visual configuration inspection**:
   ```bash
   # Inspect transitions and curves on real monitors against a running dev daemon
   tools/showcase.py --suite curves

   # Or inspect visual suites inside the isolated nested session
   tools/nested/nested-session.sh --showcase artwork
   ```
   See `tools/README.md` for showcase suites and dry-run options.

4. **Clean OS and pre-release verification**:
   ```bash
   # Boot a headless Arch Linux VM, install the checkout, and exercise the installed effect
   distrobox enter theater-mode-vm -- tools/vm/vm.sh check
   ```
   See `tools/vm/README.md` for VM provisioning and interactive graphical run commands.

### Manual CLI simulation

To test an effect on your live desktop without launching a game, use an output name printed
by `theater-mode outputs`:

```bash
theater-mode outputs
theater-mode simulate 1671210 DP-1
theater-mode clear
```

You can optionally run the full check as a pre-commit hook:

```bash
ln -sf ../../bin/check .git/hooks/pre-commit
```

### Editor integration on atomic desktops

Host and Flatpak editors do not automatically see headers, Python modules, Ruff, or
language servers installed only in `theater-mode-dev`. Configure the editor to run its
language server in that Distrobox/Toolbx environment if it supports remote containers.
Otherwise, expect host-side missing-import or missing-header diagnostics and treat
`distrobox enter theater-mode-dev -- ./bin/check` as authoritative.

Do not install duplicate tools into `~/.local/bin` merely to silence the editor: that
directory is shared with Distrobox and precedes some container paths, so it can shadow the
manifest's tool versions. The toolchain block at the start of `bin/check` shows exactly
which executable won resolution.

### Developer environment variables

Dev keys are settable **only via environment variables** and are reserved for development, test fixtures, and debugging:

| Environment Variable | Description |
| --- | --- |
| `THEATER_DEV_CONFIG_OVERRIDE` | Path to a replacement user configuration file (replaces the user layer entirely for tests). |
| `THEATER_DEV_SYSTEM_CONFIG_OVERRIDE` | Path to a replacement system configuration file. |
| `THEATER_DEV_FORCE_ART_DIR` | Path to a custom Steam library cache directory for artwork testing. |
| `THEATER_DEV_VERBOSE` | Enable verbose debug logging in daemon. |

---

## 3. Architecture at a glance

The KWin script in `kwin/theater-detect/` reports Steam window changes over D-Bus. The
Python daemon in `src/theater_mode/` decides which monitor should remain untouched,
resolves configuration, finds Steam artwork, and starts the native `theater-dimmer`
helper. The helper draws one Wayland surface on each affected monitor and removes them if
its connection to the daemon closes.

Keep desktop and hardware I/O at those boundaries so the decision-making code remains
testable without a running compositor. See
[`src/theater_mode/dimmer/PROTOCOLS.md`](src/theater_mode/dimmer/PROTOCOLS.md) before
changing the helper's Wayland protocols.

---

## 4. Dependency Tiers & Provenance

This is a first-order inventory of what `theater-mode` imports, links, invokes while
installing or building a release, and vendors. It stands in for a generated SBOM: small
enough to keep accurate by hand, and specific enough to answer where each non-platform
piece came from. It does not recursively enumerate the operating system. Platform
requirements — KWin on Plasma 6, a compositor offering `wlr-layer-shell`, systemd user
units — are described in the README.

| Tier | Component | Purpose and origin |
| --- | --- | --- |
| **Direct Runtime** | Python 3.12+ and its standard library | Daemon runtime, CLI dispatch, configuration parsing, and the updater |
| **Direct Runtime** | PyGObject (`Gio`, `GLib`, `GLibUnix`) | D-Bus IPC and the asynchronous event loop |
| **Direct Runtime** | glibc (`libc.so.6`) | C and POSIX runtime for both native helpers |
| **Direct Runtime** | `libwayland-client.so.0` | Wayland protocol transport for `theater-dimmer` |
| **Direct Runtime** | `libm.so.6` | Linked by both native helpers for the resampler and fade-curve math |
| **Transitive Runtime** | `libffi` | Required by `libwayland-client`; the distro selects its concrete soname and nothing here links it directly |
| **Install and Update** | Bash 4+, `tar`, `gzip`, `curl` or `wget`, `sha256sum` or `shasum` | Bootstrap download, verification, installation, and removal |
| **Vendored Code** | [`stb_image.h`](https://github.com/nothings/stb) v2.30 — MIT or Unlicense | JPEG and PNG decoding in `src/theater_mode/art/`. The full license text travels inside the header, which ships in the release archive. |
| **Vendored Code** | Five `wayland-scanner` bindings — MIT or HPND | Layer-shell, viewporter, single-pixel-buffer, alpha-modifier, and xdg-shell client code compiled into `theater-dimmer`. Upstream repositories, XML paths, and the regeneration procedure are recorded in [`PROTOCOLS.md`](src/theater_mode/dimmer/PROTOCOLS.md). |
| **Build Only** | C compiler, `make`, `pkg-config`, `libwayland-dev` | Compiling `theater-dimmer` and `theater-art`. GCC 12+ or Clang 9+ is needed for `_FORTIFY_SOURCE=3`; below that glibc warns and fortifies at level 2. |
| **Release Only** | Git, GNU `tar`, `gzip`, Coreutils, Findutils | Selecting tracked inputs, deterministic archive assembly, and checksum generation |
| **Check Only** | PyYAML, Node.js, `readelf` from Binutils, ShellCheck, Ruff | Workflow/schema parsing, KWin checks, ABI inspection, shell linting, and Python linting |
| **Optional** | [`gh`](https://cli.github.com) | Verifying release build provenance in `get.sh`. A `gh` that is missing, unauthenticated, or too old to carry the `attestation` command falls back to checksum verification alone. |
| **Maintainer Only** | Pillow | Rendering the reference artwork corpus (`tests/generate_reference_corpus.py`). Not needed to run `bin/check`, which compares against committed fixtures. |

The prebuilt helpers target a glibc 2.35 floor, enforced by `bin/check-abi-floor`. Systems
below that floor build the same sources through `./install.sh --build`, which is why the
helper sources ship in the release archive.

---

## 5. Code Standards

* **Modern Python**: Target Python 3.12+. Every Python file should begin with `from __future__ import annotations`.
* **Type Annotations**: Provide explicit type hints for all function signatures and class attributes.
* **Docstrings**: Follow PEP 257 format (`"""Single-line summary."""` or summary followed by blank line and detailed notes).
* **Formatting & Linting**: We use [Ruff](pyproject.toml) and ShellCheck. `bin/check` expects the development environment to provide both tools and never downloads dependencies itself.
* **Modularity**: Keep hardware I/O (DRM sysfs, the dimmer helper, D-Bus) isolated from state tracking and heuristics so logic remains unit-testable without a physical compositor.

---

## 6. Git Commit Guidelines

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
  * With a subsystem prefix: use **lowercase** after the colon (`effects: drive...`), unless starting with a proper noun or code identifier (`steam: detect AppID...`).
  * Without a prefix: **capitalize** the first word (`Initial import of theater-mode`).
* **Keep subjects concise**: Aim for 50-72 characters.
* **Approved Subsystems**:

| Prefix | Scope |
| --- | --- |
| `core:` | Package structure, shared abstractions, main runtime loop |
| `daemon:` | Lifecycle state machine, debouncing, snapshot sync |
| `effects:` | Dimmer helper, artwork, and effect construction |
| `display:` | DRM connector and mode detection |
| `steam:` | AppID detection, process inspection, Gamescope heuristics |
| `kwin:` | KWin detection script (`theater-detect`) |
| `install:` | Installer, systemd units, packaging, and releases |
| `tests:` | Unit tests, mocks, and fixtures |
| `tools:` | Developer tools, `bin/check`, Ruff linting/formatting configs |
| `docs:` | User, contributor, and inline documentation |
| `art:` | Native C artwork scaling and compositing pipeline |

### Commit Body Guidelines

* **Small, self-explanatory changes** (typos, single bug fixes) need only a concise one-line subject.
* **Non-trivial changes** should include a body that explains:
  1. **The problem / motivation**: What behavior was broken, unhandled, or missing?
  2. **The "Why" over "What"**: The diff already shows *what* changed; the commit message explains *why* this specific architecture or approach was chosen.
  3. **Compositor & Hardware quirks**: Document Wayland protocol constraints, Plasma quirks, D-Bus nuances, or Linux sandbox / procfs namespaces.
* **Formatting**:
  * Write in **natural, soft-wrapped paragraphs** (separate paragraphs with a single blank line).
  * **Do not insert manual hard line breaks mid-sentence.** Let IDEs and GitHub wrap text responsively.
  * Use markdown bullet lists (`- item`) where each item is a single, continuous line without artificial line wraps.
  * Use backticks for symbols, commands, and file paths (e.g. `theater-dimmer`, `/sys/class/drm`).

### Examples

#### Good

```text
effects: drive dimmer fades from the wall clock rather than frame counts

A compositor stops delivering frame callbacks for an output that has been switched off, which froze fades partway through and left a screen dark. Deriving progress from elapsed time lets an output that wakes up mid-transition land where it should have been.
```

```text
steam: detect AppID from Gamescope command line arguments

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

## 7. Submitting Pull Requests

1. Create a descriptive branch for your work: `git switch -c feature/my-enhancement`.
2. Ensure `./bin/check` passes cleanly with no test failures or linter warnings.
3. Write clean commits conforming to the guidelines above.
4. Submit a Pull Request with a clear description of your changes and any testing performed.
