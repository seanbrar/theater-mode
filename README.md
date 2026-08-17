# theater-mode

Dims the monitors a Steam game is **not** running on, and puts the game's own artwork on them,
restoring everything when it exits.

A KWin script reports window lifecycle; a daemon decides what any of it means and applies the
effect. All policy lives in the daemon, so the compositor side never needs changing.

Built for KDE Plasma 6 on Wayland.

## Install

```sh
./install.sh          # copy into place
./install.sh --link   # symlink instead, so edits in this repo are live
```

Everything lands under `$HOME`; nothing needs elevation and the base system is untouched. The
daemon starts in dry-run mode and changes nothing until you pick an effect.

Two steps are left to you afterwards, both deliberately manual:

1. Enable **Theater Mode Detector** in System Settings → Window Management → KWin Scripts.
2. Choose an effect with `systemctl --user edit theater-mode.service` — see
   `override.conf.example`.

`./install.sh --uninstall` removes what it installed, listing everything first and leaving your
settings, cached artwork and saved state alone.

## Repository layout

| Path | Installs to |
| --- | --- |
| `bin/theater-moded` | `~/.local/bin/theater-moded` |
| `src/theater_mode/` | `~/.local/share/theater-mode/lib/theater_mode/` |
| `kwin/theater-detect/` | `~/.local/share/kwin/scripts/theater-detect/` |
| `systemd/theater-mode.service` | `~/.config/systemd/user/theater-mode.service` |
| `README.md` | `~/.local/share/theater-mode/README.md` |
| `override.conf.example` | not installed — a template for your own settings |

The KWin script's `metadata.json` is what makes it a loadable KPackage; the directory structure
around it matters, which is why `install.sh` copies the whole `theater-detect` directory.

## Components

| Path | Role |
| --- | --- |
| `~/.local/share/kwin/scripts/theater-detect/` | KWin script. Reports windows only — no decisions. Enable it in **System Settings → Window Management → KWin Scripts**. |
| `~/.local/bin/theater-moded` | Daemon. Detection policy, state machine, effects, crash recovery. |
| `~/.config/systemd/user/theater-mode.service` | Runs the daemon. Enabled for `graphical-session.target`. |
| `~/.config/systemd/user/theater-mode.service.d/override.conf` | Your settings. Created by `systemctl --user edit`. |
| `~/.local/state/theater-mode/pending.json` | Written while an effect is applied, so a crash can be undone on next start. |

## Configuration

Set these in the drop-in (see below). Each maps to a command-line flag of the same name if you run
the daemon by hand.

| Variable | Default | What it does |
| --- | --- | --- |
| `THEATER_EFFECT` | `log` | Which effects to apply, comma separated. `log` = dry run, changes nothing. `brightness` = dim the other screens. `wallpaper` = put the game's artwork on them. Combine them: `brightness,wallpaper`. |
| `THEATER_DIM_FACTOR` | `0.35` | Fraction of each screen's current brightness to dim to. `0.35` on a screen at 75% gives 26%. Lower is darker. |
| `THEATER_SETTLE_SECONDS` | `1.5` | How long a brightness change takes to actually reach the monitor. Not a fade — it is used to time the wallpaper switch so it lands after the screens have darkened. Raise it if the wallpaper changes before the dim is visible. |
| `THEATER_STAGE_DELAY` | `1.5` | How long a game must stay on a new screen before the effect follows it. Games throw short-lived windows onto other screens while starting and stopping; this stops the dim and wallpaper being dragged around by them. |
| `THEATER_REVERT_DELAY` | `3` | Seconds to wait before restoring, so a launcher handing off to the game doesn't flash the other screens. `0` disables. |

Two more flags exist only on the command line:

| Flag | What it does |
| --- | --- |
| `--verbose` | Logs every window event, not just games. Useful when a game isn't being detected. |
| `--require-fullscreen` | Only counts a game once its window is fullscreen. **Leave this off** — games that toggle fullscreen during startup (Deltarune) will flap the effect. |

### Changing settings

```sh
systemctl --user edit theater-mode.service
```

```ini
[Service]
Environment=THEATER_EFFECT=brightness
Environment=THEATER_DIM_FACTOR=0.25
```

```sh
systemctl --user restart theater-mode.service
```

`systemctl --user revert theater-mode.service` discards the drop-in and returns to defaults.

## Checking on it

```sh
# What is it doing right now?
busctl --user call org.theatermode.TheaterMode /org/theatermode/TheaterMode \
    org.theatermode.TheaterMode Status

# Follow along while launching a game
journalctl --user -u theater-mode.service -f

# Which effect is active (printed at every startup)
journalctl --user -u theater-mode.service -n 1
```

### Testing without a game

```sh
D="busctl --user call org.theatermode.TheaterMode /org/theatermode/TheaterMode org.theatermode.TheaterMode"

$D Simulate ss "1671210" "DP-1"   # pretend a game opened on DP-1
$D Clear                          # undo it
```

`Clear` is the escape hatch: it restores the screens and forgets every tracked window, even if the
daemon wrongly believes a game is still open. The detector re-syncs within 60 seconds and re-applies
if a game really is running.

## How a game is recognised

1. Window class matching `steam_app_<appid>` — covers Proton and most native titles.
2. Otherwise, `SteamGameId` in the process environment — covers games that set their own class.
3. Otherwise, `AppId=<n>` in the process command line — covers games run inside gamescope, whose
   process starts before Steam sets any environment variables.

The Steam client itself, Big Picture and `steamwebhelper` are excluded by name, so the launcher
never counts as a game.

With gamescope, KWin never sees the game's own window — gamescope runs its own nested compositor,
so only gamescope's surface is visible, and that is what gets dimmed around.

## The wallpaper effect

Uses Steam's own library artwork, already on disk at
`~/.local/share/Steam/appcache/librarycache/<appid>/**/library_hero.jpg`. The hero is a 1920x620
letterbox, so it is composited over a blurred, darkened blow-up of itself, fitted to each screen's
native resolution and feathered at the seams. Results are cached in `~/.cache/theater-mode/`
(a few hundred KB per screen size, built once per game in well under a second).

When combined with `brightness`, the two are sequenced as one motion rather than fired together.
The dim is issued first and the wallpaper changes once it has reached the monitor
(`THEATER_SETTLE_SECONDS` later), while the screens are already dark — switching a wallpaper plugin
is atomic and cannot crossfade, so it is hidden rather than faded. On the way back the wallpaper is
restored first, while still dark, and the light comes up after.

Only the wallpaper **plugin** is switched, never the previous plugin's settings — Plasma stores each
wallpaper plugin's configuration separately, so existing plugin configurations (such as custom wallpaper
engines or playlists) survive untouched and come back on revert.

Artwork only exists locally for games whose store or library page you have actually opened in the Steam
client. A game with no cached hero art is skipped with a note in the log, and still dims normally if
`brightness` is also enabled.

## Gotchas

- **Brightness goes through `kscreen-doctor`, not `org.kde.ScreenBrightness`.** Both turn the same
  control, but powerdevil also pops the brightness OSD over the game. kscreen is silent.
- **Brightness values are whole-number percents.** `kscreen-doctor` splits arguments on `.`, so
  `output.DP-2.brightness.0.5` parses as `0` and blacks the screen out.
- **A monitor whose DDC/CI handshake failed has no brightness control** and gets skipped with a note
  in the log. Some displays or display adapters (such as passive DP→HDMI cables) may fail DDC/CI after
  hotplugging; power-cycling the monitor or using native cabling typically resolves it.
- **Brightness is set in one step, never faded.** KWin queues DDC/CI writes and delivers them to
  the monitor about 1.5 s later, so intermediate values of a software fade are simply discarded —
  the screen sits still and then jumps. Worse, a stale queued value can arrive *after* the final one
  and flash a monitor to the wrong level. One write per transition lets the monitor run its own
  internal ramp, which is what actually looks smooth.
- **What counts as "undimmed" is captured once per session of theater mode**, not re-read from the
  screen. If you change a monitor's brightness by hand *while a game is running*, the restore will
  put it back to what it was before the game started.
- The daemon restores brightness on exit and on crash. If a screen is ever stuck dim, run `Clear`
  or restart the service.

## Contributing

Contributions are welcome! Please see [CONTRIBUTING.md](CONTRIBUTING.md) for development workflows, testing, and commit guidelines.

## License

MIT. See [LICENSE](LICENSE) for details.
