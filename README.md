# theater-mode

Dims inactive monitors and displays the active Steam game's library artwork on them, restoring displays when the game exits.

A lightweight KWin script reports window lifecycle events via D-Bus to a background daemon (`theater-moded`), which controls a native Wayland layer-shell helper (`theater-dimmer`) to smoothly fade and overlay secondary displays.

Built for KDE Plasma 6.2+ on Wayland.

## Installation

```sh
curl -fsSL https://raw.githubusercontent.com/seanbrar/theater-mode/main/get.sh | bash
```

That downloads the latest release, checks it against the published checksum, installs into `$HOME`, and starts the daemon. There is no second step: the KWin script is enabled for you and cinematic dimming is active with built-in defaults.

The published x86_64 build needs no root, compiler, or package layering, so it installs the same way on ordinary distributions and on atomic ones like Bazzite, Fedora Silverblue, and SteamOS. Other architectures currently use the source installation below.

On SteamOS this means desktop mode, and only from 3.8 onward, where the Plasma session defaults to Wayland. Earlier releases default to X11, where the overlay cannot run.

Prefer not to pipe a script into a shell? Download the tarball from [Releases](https://github.com/seanbrar/theater-mode/releases), then:

```sh
tar xzf theater-mode-v*-linux-x86_64.tar.gz
cd theater-mode-v*-linux-x86_64
./install.sh
```

### Updating and removing

```sh
theater-mode update           # Upgrade to the latest release
theater-mode update --check   # Only report whether one is available
theater-mode uninstall        # Remove it, keeping your settings
```

Your configuration, service drop-ins, and artwork cache survive both. Uninstalling lists what it keeps before it removes anything.
Updating also preserves whether the service and KWin integration are currently active.

### Installing from source

A source install compiles the `theater-dimmer` helper, so it additionally needs a C compiler, `make`, `pkg-config`, and the libwayland development headers.

```sh
git clone https://github.com/seanbrar/theater-mode
cd theater-mode
./install.sh           # Build the helper and install
```

### Requirements

Python 3.12+, PyGObject, and a KDE Plasma **6.2 or newer** Wayland session. Pillow is optional; without it secondary displays dim to plain black instead of showing game artwork.

The 6.2 floor is not cosmetic: the dimmer draws through the `wp_alpha_modifier_v1` Wayland protocol, which KWin first implemented in Plasma 6.2. On 6.0 and 6.1 the daemon starts but the overlay helper exits reporting the missing protocol.

If the KDE configuration tools are unavailable, the installer says so and asks you to enable **Theater Mode Detector** once under **System Settings → Window Management → KWin Scripts**. Otherwise this is handled for you.

Check that it is working with:

```sh
theater-mode status
```

## Configuration

`theater-mode` uses a structured 3-layer TOML configuration model resolved at runtime:

1. **Built-in Defaults** (in code, sufficient to run out of the box)
2. **System Configuration** (`/etc/xdg/theater-mode/config.toml`)
3. **User Configuration** (`~/.config/theater-mode/config.toml`)

### Managing Configuration via CLI

Use the `theater-mode` command-line tool to inspect or modify configuration live over D-Bus:

```sh
# View resolved configuration with per-key provenance and origin layers
theater-mode config show

# Check configuration warnings or malformed key diagnostics
theater-mode config diagnostics

# Read a single resolved value
theater-mode config get effect.dim_factor

# Preview a setting in-session without saving to disk
theater-mode config preview effect.dim_factor 0.50

# Revert in-session preview
theater-mode config revert-preview

# Permanently commit a setting to ~/.config/theater-mode/config.toml
theater-mode config set effect.dim_factor 0.75

# Reload configuration from disk
theater-mode config reload

# List connected displays and the config sections that address each one
theater-mode outputs
```

Values are validated against the schema before they are written, so an unknown key or an
out-of-range value is refused rather than persisted. Everything applies live except
`effect.mode`, which selects the effect implementation at startup and needs
`systemctl --user restart theater-mode.service`.

### Configuration File Format

See [`config.reference.toml`](config.reference.toml) for the complete reference configuration generated directly from the schema.

```toml
[effect]
# Display effect: 'dim' (cinematic overlay) or 'log' (dry run)
mode = "dim"

# Where the effect sits: 'over_windows' or 'behind_windows' (see "Placement" below)
placement = "over_windows"

# Fraction of brightness to reduce (0.0 = no dimming, 1.0 = solid black)
dim_factor = 0.85

# Show Steam library artwork on secondary displays
art = true

[transition]
# Transition fade duration in seconds
duration = 2.0

# Easing curve: 'sine', 'quad', 'cubic', or 'linear'
curve = "sine"

[daemon]
# Grace period in seconds before restoring displays after game exits
revert_delay = 3.0

# Stability delay in seconds before following game to a new display
stage_delay = 1.5

# Require game window to enter fullscreen before activating
require_fullscreen = false

# Per-output overrides -- see "Addressing Displays" below
[outputs."Dell Inc.:DELL S2721QS:4QCPZY3"]
dim_factor = 0.50
placement = "behind_windows"
art = false
```

### Placement

`placement` decides whether the effect sits above or below your open windows:

| Value | Behavior |
| --- | --- |
| `over_windows` | Covers everything on the display, blocking its light. Windows on that screen are hidden until the game exits. |
| `behind_windows` | Paints on the desktop behind your windows, which stay visible and usable. Empty screen area shows the artwork. |

`over_windows` is the default because blocking light is the point of theater mode. Choose
`behind_windows` for a monitor you still want to read while playing — a chat window or a
guide stays fully interactive, and the artwork fills whatever the windows don't cover.

The two interact with `dim_factor`. Over windows, `dim_factor` removes light from the whole
display. Behind windows it only darkens the artwork itself, so the default `0.85` produces a
very dark wallpaper; something nearer `0.3` usually reads better there.

### Addressing Displays

Only `placement`, `dim_factor`, `art`, `duration`, and `curve` can be set per output;
`effect.mode` and the `[daemon]` keys are global.

An output's identity is read from its EDID over DRM sysfs, so displays can be addressed by
what they *are* rather than by which port they happen to occupy. Sections are matched in
this order, and the first one that exists wins:

| Priority | Section | Selects |
| --- | --- | --- |
| 1 | `[outputs."Dell Inc.:DELL S2721QS:4QCPZY3"]` | one specific panel, even among identical models |
| 2 | `[outputs."Dell Inc.:DELL S2721QS"]` | every panel of that model, stable across port swaps |
| 3 | `[outputs.DP-2]` | whatever is plugged into that connector |

Run `theater-mode outputs` to print the exact section headers for your hardware:

```
 DP-2
   Dell Inc. DELL S2721QS
   serial: 4QCPZY3
   config sections, most specific first:
     [outputs."Dell Inc.:DELL S2721QS:4QCPZY3"]
     [outputs."DEL:DELL S2721QS:4QCPZY3"]
     [outputs."Dell Inc.:DELL S2721QS"]
     [outputs."DEL:DELL S2721QS"]
     [outputs.DP-2]
```

The full vendor name (`Dell Inc.`) comes from the system PnP ID table at
`/usr/share/hwdata/pnp.ids`; the raw three-letter EDID code (`DEL`) is always accepted as
an equivalent, so a config file stays valid on hosts without that table. Displays that
report no usable EDID -- virtual outputs, some KVM switches, sleeping panels -- are still
fully supported and are addressed by connector name.

## Status & Monitoring

Check daemon status:
```sh
theater-mode status
```

Follow daemon logs:
```sh
journalctl --user -u theater-mode.service -f
```

### Testing & Simulation

Test effects without launching a game:
```sh
# Simulate a game launch (AppID 1671210 on display DP-1)
theater-mode simulate "1671210" "DP-1"

# Clear simulation and restore displays
theater-mode clear
```

`clear` resets window tracking and immediately restores all displays.

## Game Detection

Windows are identified as games using the following heuristics:
1. **Window Class**: Resource class matching `steam_app_<appid>` (Proton and native Linux titles).
2. **Environment**: `SteamGameId` or `SteamAppId` in `/proc/<pid>/environ`.
3. **Command Line**: `AppId=<appid>` in `/proc/<pid>/cmdline` (detects games launched inside Gamescope nested sessions).

Desktop shells and Steam client processes (such as `steamwebhelper` and `plasmashell`) are ignored to prevent false positives.

## Artwork Generation

When enabled, `theater-mode` locates cached Steam library hero artwork at `~/.local/share/Steam/appcache/librarycache/<appid>/**/library_hero.jpg`.

* **Compositing**: The hero image is centered over a blurred, ambient backdrop and feathered at the seams. Artwork buffers are capped at 1920×1080 and scaled to the output by the Wayland compositor.
* **Brightness**: `dim_factor` is applied directly to the image brightness during rendering so artwork darkens naturally.
* **Caching**: Generated raw frames are cached in `~/.cache/theater-mode/` per AppID and resolution.
* **Direct Mapping**: Raw premultiplied ARGB8888 frames are passed directly to `theater-dimmer` and mapped via `wl_shm` without image decoding in the display loop.
* **Fallback**: If Pillow is not installed or hero art is unavailable, displays dim to solid black.

## Architecture & Design Notes

* **Software Dimming**: Overlays are drawn as Wayland surfaces using the `wlr-layer-shell` protocol. Hardware backlight and DDC/CI states are untouched.
* **Failure Safety**: `theater-dimmer` runs as a child process with stdin connected to the daemon. If the daemon exits or is killed, the helper detects EOF, destroys its surfaces, and exits cleanly.
* **Clock-Driven Animations**: Fade transitions calculate progress using monotonic timestamps, ensuring correct restoration even if frame callbacks stall across display sleep.
* **Live Reconfiguration**: Configuration updates, previews, and queries are served over D-Bus and re-applied to running effects without restarting the daemon.

## Components & File Layout

| Path | Role |
| --- | --- |
| `~/.local/bin/theater-mode` | Client CLI for status, simulation, and live configuration over D-Bus. |
| `~/.local/bin/theater-moded` | Python daemon managing state machine and D-Bus interfaces. |
| `~/.local/bin/theater-dimmer` | Native C Wayland helper rendering overlay surfaces. |
| `~/.local/share/kwin/scripts/theater-detect/` | KWin script reporting window events. |
| `/sys/class/drm/card*-*/edid` | Read-only source for per-display identity matching. |
| `~/.local/share/theater-mode/install.sh` | Copy of the installer, so `theater-mode uninstall` works without the release archive. |
| `~/.config/theater-mode/config.toml` | User configuration file. |
| `~/.config/systemd/user/theater-mode.service` | Systemd user service unit. |
| `~/.cache/theater-mode/` | Generated artwork cache. |

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for development workflows, testing, and commit guidelines.

## License

MIT. See [LICENSE](LICENSE) for details.
