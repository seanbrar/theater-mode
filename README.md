# theater-mode

Dims inactive monitors and displays the active Steam game's library artwork on them, restoring displays when the game exits.

A lightweight KWin script reports window lifecycle events via D-Bus to a background daemon (`theater-moded`), which controls a native Wayland layer-shell helper (`theater-dimmer`) to smoothly fade and overlay secondary displays.

Built for KDE Plasma 6 on Wayland.

## Installation

```sh
./install.sh          # Copy files into place
./install.sh --link   # Symlink files for live development
```

Files are installed to standard user locations under `$HOME` without root privileges. The daemon runs in dry-run mode (`log`) by default until configured.

To uninstall:
```sh
./install.sh --uninstall
```

### Post-Install Setup

1. **Enable the KWin Script**:
   Open **System Settings → Window Management → KWin Scripts** and enable **Theater Mode Detector**.

2. **Configure an Effect**:
   Create a systemd user drop-in:
   ```sh
   systemctl --user edit theater-mode.service
   ```
   Add:
   ```ini
   [Service]
   Environment=THEATER_EFFECT=dim
   ```
   Restart the service:
   ```sh
   systemctl --user restart theater-mode.service
   ```
   See [`override.conf.example`](override.conf.example) for additional options.

## Configuration

Set configuration variables in the systemd drop-in or as command-line flags to `theater-moded`:

| Variable | Default | Description |
| --- | --- | --- |
| `THEATER_EFFECT` | `log` | Active effect: `dim` (cinematic Wayland fade with game artwork) or `log` (dry run). |
| `THEATER_ART` | `--art` | `--art` shows the game's Steam library artwork on dimmed displays; `--no-art` dims to flat black. |
| `THEATER_DIM_FACTOR` | `0.85` | Fraction of display brightness to reduce (`0.0` = no dimming, `1.0` = black, `0.85` = 15% brightness remaining). Darkens artwork directly when artwork is enabled. |
| `THEATER_DIM_DURATION` | `2.0` | Fade transition duration in seconds. |
| `THEATER_DIM_CURVE` | `sine` | Easing curve: `sine`, `quad`, `cubic`, or `linear`. |
| `THEATER_STAGE_DELAY` | `1.5` | Stability delay (seconds) before following a game window to a new display. |
| `THEATER_REVERT_DELAY` | `3` | Grace period (seconds) before restoring displays after game windows close (`0` disables). |

Additional command-line flags:

| Flag | Description |
| --- | --- |
| `--verbose` | Enable debug logging for all window events. |
| `--require-fullscreen` | Only activate when the game window enters fullscreen. |

## Status & Monitoring

Check daemon status via D-Bus:
```sh
busctl --user call org.theatermode.TheaterMode /org/theatermode/TheaterMode \
    org.theatermode.TheaterMode Status
```

Follow daemon logs:
```sh
journalctl --user -u theater-mode.service -f
```

### Testing & Simulation

Test effects without launching a game:
```sh
# Simulate a game launch (AppID 1671210 on display DP-1)
busctl --user call org.theatermode.TheaterMode /org/theatermode/TheaterMode \
    org.theatermode.TheaterMode Simulate ss "1671210" "DP-1"

# Clear simulation and restore displays
busctl --user call org.theatermode.TheaterMode /org/theatermode/TheaterMode \
    org.theatermode.TheaterMode Clear
```

`Clear` resets window tracking and immediately restores all displays.

## Game Detection

Windows are identified as games using the following heuristics:
1. **Window Class**: Resource class matching `steam_app_<appid>` (Proton and native Linux titles).
2. **Environment**: `SteamGameId` or `SteamAppId` in `/proc/<pid>/environ`.
3. **Command Line**: `AppId=<appid>` in `/proc/<pid>/cmdline` (detects games launched inside Gamescope nested sessions).

Desktop shells and Steam client processes (such as `steamwebhelper` and `plasmashell`) are ignored to prevent false positives.

## Artwork Generation

When enabled, `theater-mode` locates cached Steam library hero artwork at `~/.local/share/Steam/appcache/librarycache/<appid>/**/library_hero.jpg`.

* **Compositing**: The hero image is centered over a blurred, ambient backdrop sized to the target display's native resolution and feathered at the seams.
* **Brightness**: `THEATER_DIM_FACTOR` is applied directly to the image brightness during rendering so artwork darkens naturally.
* **Caching**: Generated raw frames are cached in `~/.cache/theater-mode/` per AppID and resolution.
* **Direct Mapping**: Raw premultiplied ARGB8888 frames are passed directly to `theater-dimmer` and mapped via `wl_shm` without image decoding in the display loop.
* **Fallback**: If Pillow is not installed or hero art is unavailable, displays dim to solid black.

## Architecture & Design Notes

* **Software Dimming**: Overlays are drawn as Wayland surfaces using the `wlr-layer-shell` protocol. Hardware backlight and DDC/CI states are untouched.
* **Failure Safety**: `theater-dimmer` runs as a child process with stdin connected to the daemon. If the daemon exits or is killed, the helper detects EOF, destroys its surfaces, and exits cleanly.
* **Clock-Driven Animations**: Fade transitions calculate progress using monotonic timestamps, ensuring correct restoration even if frame callbacks stall across display sleep.
* **Stateless**: Display configuration and tracking remain purely in memory; no persistent desktop settings are modified.

## Components & File Layout

| Path | Role |
| --- | --- |
| `~/.local/bin/theater-moded` | Python daemon managing state machine and D-Bus interfaces. |
| `~/.local/bin/theater-dimmer` | Native C Wayland helper rendering overlay surfaces. |
| `~/.local/share/kwin/scripts/theater-detect/` | KWin script reporting window events. |
| `~/.config/systemd/user/theater-mode.service` | Systemd user service unit. |
| `~/.cache/theater-mode/` | Generated artwork cache. |

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for development workflows, testing, and commit guidelines.

## License

MIT. See [LICENSE](LICENSE) for details.
