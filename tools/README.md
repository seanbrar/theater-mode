# tools — developer workflows

These tools cover different kinds of evidence. They complement the automated unit tests
and do not substitute for one another.

| Tool | Question | Cadence |
| --- | --- | --- |
| `showcase.py` | *Do configuration choices look and transition correctly on real displays?* | Effect changes |
| `nested/` | *Does a chosen operation cross the real KWin, D-Bus, Wayland, and DRM boundaries correctly?* | Relevant integration changes |
| `vm/` | *Does a clean install run and apply its effect on another operating system?* | Before a release |

`nested/` and `vm/` both answer unattended and exit non-zero on failure:

```sh
tools/nested/nested-session.sh --check
distrobox enter theater-mode-vm -- tools/vm/vm.sh check
```

---

## Configuration showcase (`showcase.py`)

`showcase.py` steps through visual configuration scenarios against a running
daemon. It temporarily applies session preview settings and simulates game focus changes,
then independently clears the simulation and reverts the preview on exit or interrupt.
It exits non-zero and reports what remains if either cleanup operation fails.

### Prerequisites

1. A running development daemon (`theater-moded` or `systemctl --user start theater-mode`).
2. At least two enabled outputs (physical or synthetic).
3. No active game windows tracked by the daemon (`theater-mode clear`).
4. No active session preview settings (`theater-mode config revert-preview`).
5. A positive Steam AppID, supplied by `--appid <id>` or inferred from cached Steam
   artwork. Suites containing artwork also need `library_hero.jpg` cached for that AppID.

### Common recipes

```sh
# List all available showcase suites
tools/showcase.py --list

# Step through transition curves interactively (advances on Enter)
tools/showcase.py --suite curves

# Auto-advance through artwork scaling every 3 seconds with a specific Steam AppID
tools/showcase.py --suite artwork --appid 440 --interval 3

# Specify the primary display hosting the simulated game
tools/showcase.py --suite placement --game-output DP-1

# Offline topology check (requires no running daemon or physical monitors)
tools/showcase.py --dry-run --output DP-1 --output DP-2 --suite outputs --interval 0.01

# Inspect artwork in an isolated nested session without touching host monitors
tools/nested/nested-session.sh --showcase artwork
```

---

## Scope & verification boundaries

| Environment | What it verifies | Limitations |
| --- | --- | --- |
| **Physical Displays (`showcase.py`)** | Rendering on real panels and subjective transition quality. | Requires multi-monitor hardware; modifies live session during run. |
| **Nested Harness (`tools/nested/`)** | Wayland layer-shell setup, DRM connector matching, D-Bus IPC, and configured EDID fault cases in isolation. | Borrows host libc, Plasma build, and session manager; does not exercise real Steam transitions or inspect rendered pixels. |
| **Arch Guest VM (`tools/vm/`)** | Clean `install.sh` on a foreign distribution, ABI floor against external glibc, SDDM autologin, and systemd units. | Slower to provision; composited in a QEMU window (not an authority for visual aesthetics). |
