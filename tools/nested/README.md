# tools/nested — nested compositor harness

Runs the daemon, detector, and native helpers against an isolated nested Plasma compositor with synthetic displays.

A run completes in ~15 seconds, installs nothing, and leaves no persistent changes on your live desktop. Use this harness to verify that changes correctly cross KWin, D-Bus, Wayland layer-shell, and DRM boundaries under controlled display topologies.

---

## Quick run

```sh
# Unattended pass/fail assertion (exits non-zero on failure)
tools/nested/nested-session.sh --check

# Interactive session: two 1280x720 output windows appear on your desktop
tools/nested/nested-session.sh
```

---

## Prerequisites

Build both native helpers before running the harness:

**Native:**
```sh
make -C src/theater_mode/dimmer && make -C src/theater_mode/art
```

**Distrobox:**
```sh
distrobox enter theater-mode-dev -- sh -c \
    'make -C src/theater_mode/dimmer && make -C src/theater_mode/art'
```

*Note: Always run the `nested-session.sh` script from a Plasma host terminal (not inside Distrobox) so it can access host `kwin_wayland` and `bwrap`.*

---

## Common recipes

```sh
# Run visual showcase suites inside the isolated nested session
tools/nested/nested-session.sh --showcase artwork

# Test a multi-display topology with corrupted and missing EDID blocks
tools/nested/nested-session.sh --check --profile triple-mixed

# Headless verification for CI or environments without a graphical display
tools/nested/nested-session.sh --check --headless

# Seed the private daemon with a custom configuration file
tools/nested/nested-session.sh --check --config /path/to/custom-config.toml

# Retain temporary scratch files and logs for post-mortem debugging
tools/nested/nested-session.sh --check --keep
```

---

<details>
<summary>How environment isolation works</summary>

The harness constructs a fully contained desktop stack:

* **Nested compositor:** Launches `kwin_wayland` with `--output-count N` and `--socket`, advertising real `wl_output` (version 4) and `zwlr_layer_shell_v1` surfaces.
* **Synthetic sysfs:** `fake-drm.py` generates checksum-valid EDID trees for the selected profile, bind-mounted over `/sys/class/drm` via `bwrap` so the daemon's DRM enumeration matches compositor outputs.
* **Private D-Bus & XDG:** Runs inside its own `dbus-run-session` with private `XDG_CONFIG_HOME`, `XDG_DATA_HOME`, `XDG_CACHE_HOME`, and `XDG_STATE_HOME`. The nested KWin script and daemon configuration do not interact with host files.
* **Mocked Steam game:** Launches a lightweight desktop process with `SteamGameId` in its environment. The daemon detects this via `/proc/<pid>/environ` without requiring Steam or a game library.

</details>

<details>
<summary>Display profiles and command options reference</summary>

### Display profiles (`tools/nested/profiles/`)

| Profile | Connectors | Coverage |
| --- | --- | --- |
| `dual` *(default)* | `WL-0`, `WL-1` | Standard multi-monitor setup with full `make:model:serial` EDID resolution across two vendors. |
| `single` | `WL-0` | Single-monitor setup where dimming effects must remain strictly inert. |
| `triple-mixed` | `WL-0`, `WL-1`, `WL-2` | Three displays including one corrupted EDID checksum and one missing EDID block. |
| `unknown-vendor` | `WL-0`, `WL-1` | Displays with unlisted PnP vendor codes to test raw 3-letter fallback matching. |

Add a JSON file to that directory for a custom profile. Set `"corrupt": "checksum" |
"truncated" | "header"` or `"edid": false` on an output to inject EDID faults. Headless runs
use KWin's `Virtual-N` connector names rather than the windowed backend's `WL-N`; the harness
rewrites the selected profile to match.

### Command options

| Option | Default | Description |
| --- | --- | --- |
| `--check` | *interactive* | Run unattended, assert effect activation, and exit non-zero on failure. |
| `--headless` | *windowed* | Render to a virtual framebuffer instead of host desktop windows (implies `--check`). |
| `--profile NAME` | `dual` | Choose display profile from `tools/nested/profiles/`. |
| `--showcase SUITE` | *none* | Run a `showcase.py` suite against the private daemon. |
| `--config FILE` | *none* | Seed the private daemon with a custom configuration TOML. |
| `--geometry WxH` | `1280x720` | Resolution of each nested output. |
| `--appid ID` | `440` | Steam AppID reported by the fake game. |
| `--game CMD` | *auto* | Command to launch as the fake game (`kwrite`, `kate`, or `konsole`). |
| `--xwayland` | *off* | Start Xwayland alongside the nested compositor. The harness does not discover its `DISPLAY` or force the fake game onto X11, so this does not by itself verify an X11 or Proton client path. |
| `--timeout SECONDS` | `15` | Deadline for compositor startup, daemon readiness, and effect activation. |
| `--keep` | *off* | Preserve the temporary scratch directory and print its path on exit. |

</details>
