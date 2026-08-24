# tools/nested — nested compositor harness

Runs the daemon, detector, and native helpers from this checkout against an isolated nested
Plasma compositor with synthetic displays. A run completes in ~15 seconds, downloads nothing,
installs nothing, and leaves no persistent changes on the live desktop. Windowed runs display
the synthetic outputs in temporary windows.

Use this harness to verify that changes correctly cross KWin, D-Bus, Wayland layer-shell,
and DRM boundaries under controlled display topologies.

---

## Prerequisites

Build both native helpers before running the harness. The harness refuses to run with
missing or system-installed binaries:

```sh
distrobox enter theater-mode-dev -- sh -c \
    'make -C src/theater_mode/dimmer && make -C src/theater_mode/art'
```

Run the harness commands below from a Plasma host terminal, not from inside
`theater-mode-dev`. The host supplies `kwin_wayland`, `bwrap`, `dbus-run-session`, and the
KDE application used as the default fake game. Use `--headless` when that host has no
graphical display.

---

## Common recipes

```sh
# Unattended pass/fail assertion (exits non-zero on failure)
tools/nested/nested-session.sh --check

# Headless verification for CI or environments without a graphical display
tools/nested/nested-session.sh --check --headless

# Interactive session: two 1280x720 output windows appear on your desktop
tools/nested/nested-session.sh

# Test a multi-display topology with corrupted and missing EDID blocks
tools/nested/nested-session.sh --check --profile triple-mixed

# Seed the private daemon with a custom configuration file
tools/nested/nested-session.sh --check --config /path/to/custom-config.toml

# Run visual showcase suites inside the isolated nested session
tools/nested/nested-session.sh --showcase artwork

# Start Xwayland alongside the nested compositor for manual investigation
tools/nested/nested-session.sh --xwayland

# Retain temporary scratch files and logs for post-mortem debugging
tools/nested/nested-session.sh --check --keep
```

`--xwayland` starts the server, but the harness does not yet discover its `DISPLAY` or
force the fake game to use X11. It therefore does not by itself verify an X11 or Proton
client path.

---

## Environment isolation

The harness constructs a fully contained desktop stack:

* **Nested compositor:** Launches `kwin_wayland` with `--output-count N` and `--socket`, advertising real `wl_output` (version 4) and `zwlr_layer_shell_v1` surfaces.
* **Synthetic sysfs:** `fake-drm.py` generates checksum-valid EDID trees for the selected profile, bind-mounted over `/sys/class/drm` via `bwrap` so the daemon's DRM enumeration matches compositor outputs.
* **Private D-Bus & XDG:** Runs inside its own `dbus-run-session` with private `XDG_CONFIG_HOME`, `XDG_DATA_HOME`, `XDG_CACHE_HOME`, and `XDG_STATE_HOME`. The nested KWin script and daemon configuration do not interact with host files.
* **Mocked Steam game:** Launches a lightweight desktop process with `SteamGameId` in its environment. The daemon detects this via `/proc/<pid>/environ` without requiring Steam or a game library.

---

## Display profiles

Profiles in `tools/nested/profiles/` describe synthetic connector topologies:

| Profile | Connectors | Coverage |
| --- | --- | --- |
| `dual` *(default)* | `WL-0`, `WL-1` | Standard multi-monitor setup with full `make:model:serial` EDID resolution across two vendors. |
| `single` | `WL-0` | Single-monitor setup where dimming effects must remain strictly inert. |
| `triple-mixed` | `WL-0`, `WL-1`, `WL-2` | Three displays including one corrupted EDID checksum and one missing EDID block. |
| `unknown-vendor` | `WL-0`, `WL-1` | Displays with unlisted PnP vendor codes to test raw 3-letter fallback matching. |

Headless mode uses KWin's `Virtual-N` connector names instead of the windowed backend's
`WL-N` names. The harness rewrites the selected profile accordingly.

To create a custom profile, add a JSON file to `tools/nested/profiles/`. Set `"corrupt": "checksum" | "truncated" | "header"` or `"edid": false` to inject EDID faults.

---

## Command options

| Option | Default | Description |
| --- | --- | --- |
| `--check` | *interactive* | Run unattended, assert effect activation, and exit non-zero on failure. |
| `--headless` | *windowed* | Render to a virtual framebuffer instead of host desktop windows (implies `--check`). |
| `--profile NAME` | `dual` | Choose display profile from `tools/nested/profiles/`. |
| `--showcase SUITE` | *none* | Run a `showcase.py` suite against the private daemon. |
| `--config FILE` | *none* | Seed the private daemon with a custom configuration TOML. |
| `--geometry WxH` | `1280x720` | Resolution of each nested output. |
| `--appid ID` | `440` | Steam AppID reported by the fake game. |
| `--game CMD` | *auto* | Command to launch as the fake game (defaults to `kwrite`, `kate`, or `konsole`). |
| `--xwayland` | *off* | Start Xwayland alongside the nested compositor; see the limitation above. |
| `--timeout SECONDS` | `15` | Deadline for compositor startup, daemon readiness, and effect activation. |
| `--keep` | *off* | Preserve the temporary scratch directory and print its path on exit. |
