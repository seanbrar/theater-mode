# Contributing to theater-mode

Bug fixes, hardware quirk workarounds, effect enhancements, tests, and documentation
improvements are welcome. This guide describes the workflow enforced by CI.

---

## 1. Get a working development environment

Clone the repository and choose your setup below. KDE Plasma is required only for live desktop testing; all automated checks run headless.

### Option A: native toolchain (standard distributions)

Install the required build, testing, and linting tools for your distribution:

**Fedora / RHEL / Nobara:**
```bash
sudo dnf install gcc make pkgconf-pkg-config python3 python3-pyyaml wayland-devel binutils nodejs ShellCheck pipx
```

**Arch Linux / CachyOS:**
```bash
sudo pacman -S gcc make pkgconf python python-yaml wayland binutils nodejs shellcheck python-pipx
```

**Ubuntu / Debian:**
```bash
sudo apt install build-essential pkg-config python3 python3-yaml libwayland-dev binutils nodejs shellcheck pipx
```
Then install Ruff on any of them. `pyproject.toml` accepts one minor version and Ruff
refuses to run outside it, so pin the install rather than taking a distribution's build:

```bash
pipx install 'ruff>=0.16,<0.17'
```

Once installed, run the authoritative test suite:

```bash
./bin/check
```

---

### Option B: Distrobox (Bazzite, SteamOS, atomic distributions)

On an atomic or containerized distribution, use the repository's container manifest to avoid layering packages onto your host:

```bash
# 1. Create the container once
distrobox assemble create --file distrobox.ini

# 2. Run the test suite
distrobox enter theater-mode-dev -- ./bin/check
```

<details>
<summary>Distrobox tips, isolated installer tests, and troubleshooting</summary>

#### Testing the installer in isolation
The container shares your real home directory. Running `./install.sh` inside it installs into your real `~/.local` and can restart your host's user service. To test the installer in isolation, redirect `$HOME`:

```bash
fake_home=$(mktemp -d)
env -u XDG_DATA_HOME -u XDG_CONFIG_HOME -u XDG_BIN_HOME -u XDG_CACHE_HOME \
    HOME="$fake_home" ./install.sh --no-service
env -u XDG_DATA_HOME -u XDG_CONFIG_HOME -u XDG_BIN_HOME -u XDG_CACHE_HOME \
    HOME="$fake_home" "$fake_home/.local/share/theater-mode/install.sh" \
    --uninstall --no-service --yes
```

#### Updating the container definition
If you edit `distrobox.ini`, remove and recreate the container. Distrobox does not apply
manifest changes to an existing container, and `--replace` does not override the manifest's
`replace=false`:

```bash
distrobox assemble rm --file distrobox.ini
distrobox assemble create --file distrobox.ini
```

#### Tool shadowing
Distrobox places `~/.local/bin` early in `$PATH`, so a tool installed on your host can shadow the container's copy. `./bin/check` prints the resolved path and version of every tool; compare that first block with CI if results differ.

</details>

---

## 2. Make and test a change

### Fast iteration
Run focused unit tests while iterating (no Plasma or running daemon required):

```bash
python3 -m unittest tests.test_config -v
python3 -m unittest discover -s tests -t . -q
```

### Full verification
Before committing or opening a Pull Request, run the same repository-wide entry point used by CI:

```bash
./bin/check
```
*(Or `distrobox enter theater-mode-dev -- ./bin/check` on atomic systems).*

It builds `theater-dimmer` and `theater-art` with warnings treated as errors, verifies their version and ABI floor, runs ASan/UBSan unit tests and oracle verification, checks the generated configuration reference and KWin JavaScript, runs ShellCheck and all unit tests, then checks Python linting and formatting with Ruff.

---

### Live desktop testing

Live testing changes your current user installation.

**On mutable systems (Native toolchain):**
Reinstall directly from the checkout:
```bash
./install.sh
theater-mode status
journalctl --user -u theater-mode.service -f
```

**On atomic systems (Distrobox):**
The checkout has no prebuilt helpers and the host intentionally lacks the compiler toolchain. Build in Distrobox, then activate the result from a host terminal:
```bash
distrobox enter theater-mode-dev -- sh -c 'make -C src/theater_mode/dimmer && make -C src/theater_mode/art'
./install.sh --dimmer-bin=src/theater_mode/dimmer/theater-dimmer --art-bin=src/theater_mode/art/theater-art
```
The second command must run on the host because the container lacks the host's KDE configuration tools and session bus. `./bin/check` also builds and verifies both helpers, so a run of it replaces the first command.

---

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

<details>
<summary>Editor integration on atomic desktops</summary>

Host and Flatpak editors do not automatically see headers, Python modules, Ruff, or
language servers installed only in `theater-mode-dev`. Configure the editor to run its
language server in that Distrobox/Toolbx environment if it supports remote containers.
Otherwise, expect host-side missing-import or missing-header diagnostics and treat
`distrobox enter theater-mode-dev -- ./bin/check` as authoritative.

Do not install duplicate tools into `~/.local/bin` merely to silence the editor: that
directory is shared with Distrobox and precedes some container paths, so it can shadow the
manifest's tool versions. The toolchain block at the start of `bin/check` shows exactly
which executable won resolution.

</details>

<details>
<summary>Developer environment variables</summary>

Dev keys are settable **only via environment variables** and are reserved for development, test fixtures, and debugging:

| Environment Variable | Description |
| --- | --- |
| `THEATER_DEV_CONFIG_OVERRIDE` | Path to a replacement user configuration file (replaces the user layer entirely for tests). |
| `THEATER_DEV_SYSTEM_CONFIG_OVERRIDE` | Path to a replacement system configuration file. |
| `THEATER_DEV_FORCE_ART_DIR` | Path to a custom Steam library cache directory for artwork testing. |
| `THEATER_DEV_VERBOSE` | Enable verbose debug logging in daemon. |

</details>

---

## 3. Architecture at a glance

The KWin script in `kwin/theater-detect/` reports Steam window changes over D-Bus. The
Python daemon in `src/theater_mode/` decides which monitor should remain untouched,
resolves configuration, finds Steam artwork, and starts the native `theater-dimmer`
helper. The helper draws one Wayland surface on each affected monitor and removes them if
its connection to the daemon closes.

Keep desktop and hardware I/O cleanly isolated from state tracking so logic remains
testable without a physical compositor. See
[`src/theater_mode/dimmer/PROTOCOLS.md`](src/theater_mode/dimmer/PROTOCOLS.md) before
changing Wayland protocols.

---

## 4. Dependencies & Provenance

`theater-mode` avoids heavy runtime dependencies. The daemon runs on standard Python 3.12+
with pure-Python D-Bus handling (`jeepney` is vendored), and the native C helpers link only
standard platform libraries (`libc`, `libm`, `libwayland-client`).

<details>
<summary>Full inventory: runtime, vendored code, and tooling</summary>

A first-order inventory of what `theater-mode` imports, links, invokes, and vendors. It
stands in for a generated SBOM and does not recursively enumerate the operating system.
Platform requirements (KWin on Plasma 6, `wlr-layer-shell`, systemd user units) are
described in the README.

#### Runtime

What a running installation needs.

| Component | Source | Used for |
| --- | --- | --- |
| Python 3.12+ and its standard library | Platform | Daemon runtime, CLI dispatch, configuration parsing, and the updater |
| `jeepney` | Vendored, `src/theater_mode/_vendor/` | D-Bus message construction and transport. Pure Python, so the daemon needs nothing outside CPython; the event loop is this project's own `theater_mode.bus.EventLoop` |
| `libc.so.6` (glibc) | Platform | C and POSIX runtime for both native helpers |
| `libwayland-client.so.0` | Platform | Wayland protocol transport for `theater-dimmer` |
| `libm.so.6` | Platform | Resampler and fade-curve math in both native helpers |
| `libffi` | Platform, transitive through `libwayland-client` | Nothing here links it directly; the distribution selects its concrete soname |

The prebuilt helpers target a glibc 2.35 floor, enforced by `bin/check-abi-floor`. Systems
below that floor build the same sources through `./install.sh --build`, which is why the
helper sources ship in the release archive.

#### Third-party code in this repository

Vendored source, its license, and how to regenerate it. `jeepney` appears here and above
because it both ships in-tree and runs at runtime.

| Component | License | Upstream and regeneration |
| --- | --- | --- |
| `jeepney` | MIT | Rebuilt by `bin/vendor-jeepney` from the version pinned in `.github/vendor/requirements.txt` |
| [`stb_image.h`](https://github.com/nothings/stb) v2.30 | MIT or Unlicense | Decodes JPEG and PNG in `src/theater_mode/art/`. The full license text travels inside the header, which ships in the release archive |
| Five `wayland-scanner` bindings | MIT or HPND | Layer-shell, viewporter, single-pixel-buffer, alpha-modifier, and xdg-shell client code compiled into `theater-dimmer`. Repositories, XML paths, and the regeneration procedure are in [`PROTOCOLS.md`](src/theater_mode/dimmer/PROTOCOLS.md) |

#### Tooling

Nothing here ships to a user. The build and check tools are the ones
[section 1](#1-get-a-working-development-environment) installs.

| When | Component | Used for |
| --- | --- | --- |
| Install and update | Bash 4+, `tar`, `gzip`, `curl` or `wget`, `sha256sum` or `shasum` | Bootstrap download, verification, installation, and removal |
| Install and update, optional | [`gh`](https://cli.github.com) | Verifying release build provenance in `get.sh`. A `gh` that is missing, unauthenticated, or too old to carry the `attestation` command falls back to checksum verification alone |
| Build | C compiler, `make`, `pkg-config`, `libwayland-dev` | Compiling `theater-dimmer` and `theater-art`. GCC 12+ or Clang 9+ is needed for `_FORTIFY_SOURCE=3`; below that glibc warns and fortifies at level 2 |
| Check | PyYAML, Node.js, `readelf` from Binutils, ShellCheck, Ruff | Workflow and schema parsing, KWin checks, ABI inspection, shell linting, and Python linting |
| Release | Git, GNU `tar`, `gzip`, Coreutils, Findutils | Selecting tracked inputs, deterministic archive assembly, and checksum generation |
| Maintainer only | Pillow | Rendering the reference artwork corpus (`tests/generate_reference_corpus.py`). Not needed to run `bin/check`, which compares against committed fixtures |
| Maintainer only | `pip`, `unzip` | Downloading and unpacking the jeepney wheel in `bin/vendor-jeepney`. Needed only when the vendored copy is updated |

</details>

---

## 5. Code Standards

* **Modern Python**: Target Python 3.12+. Every Python file begins with `from __future__ import annotations`.
* **Type Annotations**: Provide explicit type hints for all function signatures and class attributes.
* **Docstrings**: Follow PEP 257 format (`"""Single-line summary."""` or summary followed by blank line and detailed notes).
* **Formatting & Linting**: Python is formatted and linted with [Ruff](pyproject.toml); shell is linted with ShellCheck. `./bin/check` runs both and never downloads a dependency itself, so the environment has to provide them.
* **Hardware Isolation**: Keep hardware I/O (DRM sysfs, D-Bus, Wayland protocols) isolated from heuristics and state machines to preserve mockability in unit tests.

---

## 6. Git Commit Guidelines

We follow a **Modern Markdown Git style**: concise, imperative subjects with a body focused on rationale and architectural context.

### Subject Line Format
`<subsystem>: <imperative summary under 50-72 chars>`

* **Imperative mood**: *"Add support..."*, *"Fix race condition..."* (not *"Added"* or *"Fixes"*).
* **Capitalization**: Lowercase after prefix colon (`effects: drive...`), unless starting with a proper noun or code symbol (`steam: detect AppID...`).
* **No trailing period**.
* **Common subsystems**: `core:`, `daemon:`, `effects:`, `display:`, `steam:`, `kwin:`, `install:`, `tests:`, `tools:`, `docs:`, `art:`.

### Body Guidelines
* **Why over What**: The diff shows *what* changed; the body explains the motivation, trade-offs, and compositor quirks.
* **Markdown paragraphs**: Write in natural, soft-wrapped markdown paragraphs separated by blank lines (no manual hard line breaks mid-sentence). Use backticks for symbols and file paths.

### Example

```text
effects: drive dimmer fades from the wall clock rather than frame counts

A compositor stops delivering frame callbacks for an output that has been switched off, which froze fades partway through and left a screen dark. Deriving progress from elapsed time lets an output that wakes up mid-transition land where it should have been.
```

---

## 7. Submitting Pull Requests

1. Create a branch: `git switch -c feature/my-enhancement`.
2. Ensure `./bin/check` passes cleanly with no test failures or linter warnings.
3. Write clean commits conforming to the guidelines above.
4. Open a Pull Request with a clear summary of changes and testing performed.
