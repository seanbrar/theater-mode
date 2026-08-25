# tools — developer workflows and verification

These tools complement automated unit tests by verifying behavior across real and synthetic desktop boundaries.

| Tool | Question | Cadence | Quick Command |
| --- | --- | --- | --- |
| **`showcase.py`** | *Do visual transitions and curves look right on real displays?* | Effect changes | `tools/showcase.py --suite curves` |
| **`nested/`** | *Do changes cross KWin, D-Bus, Wayland, and DRM cleanly?* | Integration changes | `tools/nested/nested-session.sh --check` |
| **`vm/`** | *Does a clean install work on an independent distribution?* | Pre-release | `distrobox enter theater-mode-vm -- tools/vm/vm.sh check` |

---

## 1. Visual configuration showcase (`showcase.py`)

`showcase.py` steps through visual configuration scenarios against a running daemon without permanent config changes. It applies temporary session previews, simulates game focus events, and automatically reverts on exit. If either cleanup step fails it says what remains and exits non-zero.

### Prerequisites

1. A running daemon (`theater-moded` or `systemctl --user start theater-mode`).
2. At least two enabled displays (physical or synthetic).
3. No active game windows tracked (`theater-mode clear`).
4. No active session preview settings (`theater-mode config revert-preview`).
5. A Steam AppID, from `--appid <id>` or inferred from cached artwork. Artwork suites also
   need `library_hero.jpg` cached for that AppID.

### Common recipes

```sh
# List all available showcase suites
tools/showcase.py --list

# Step through transition curves interactively (Enter: next, p: prev, r: replay, q: quit)
tools/showcase.py --suite curves

# A/B compare flat dark overlays against ambient hero artwork
tools/showcase.py --suite compare

# Auto-advance through artwork scaling every 3 seconds for Steam AppID 440 (Team Fortress 2)
tools/showcase.py --suite artwork --appid 440 --interval 3

# Specify the primary display hosting the simulated game
tools/showcase.py --suite placement --game-output DP-1

# Offline topology check (requires no running daemon or physical monitors)
tools/showcase.py --dry-run --output DP-1 --output DP-2 --suite outputs --interval 0.01

# Inspect artwork inside an isolated nested compositor without touching host monitors
tools/nested/nested-session.sh --showcase artwork
```

---

## 2. Scope & verification boundaries

| Environment | What it verifies | Limitations |
| --- | --- | --- |
| **Physical Displays (`showcase.py`)** | Rendering on real panels and subjective transition quality. | Requires multi-monitor hardware; modifies live session during run. |
| **Nested Harness (`tools/nested/`)** | Wayland layer-shell setup, DRM connector matching, D-Bus IPC, and configured EDID fault cases in isolation. | Borrows host libc, Plasma build, and session manager; does not exercise real Steam transitions or inspect rendered pixels. |
| **Arch Guest VM (`tools/vm/`)** | Clean `install.sh` on a foreign distribution, ABI floor against external glibc, SDDM autologin, and systemd units. | Slower to provision; composited in a QEMU window (not an authority for visual aesthetics). |
