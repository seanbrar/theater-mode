# theater-mode

When you launch a Steam game, theater-mode dims your other monitors and fills them with
the game's Steam artwork. Move the game to another monitor and the effect follows it.
Close the game and everything returns to normal.

## Install

Open a terminal, paste this command, and press Enter:

```sh
curl -fsSL https://raw.githubusercontent.com/seanbrar/theater-mode/main/get.sh | bash
```

The installer verifies compatibility, sets up the background service, and starts
theater-mode. It installs only for your user account and does not need `sudo` or change
the operating system.

Now launch a Steam game. The other monitors fade after its window appears.

The ready-made download is for x86_64 systems, which covers ordinary desktop PCs, the
Steam Deck, and most Bazzite devices. On another processor, see
[Installing from source](#installing-from-source).

theater-mode works with:
- two or more monitors;
- KDE Plasma 6.2 or newer on a Wayland session (Bazzite KDE, SteamOS 3.8+ Desktop Mode,
  Fedora KDE, Nobara, and Arch all qualify); and
- games launched through Steam.

The installer checks your desktop and stops with an explanation if it is not compatible.
It cannot check where you play, so: on the Steam Deck, theater-mode works in **Desktop
Mode** and does nothing in Game Mode. The Bazzite GNOME edition is not supported.

<details>
<summary>Manual download & checksum verification</summary>

Download the archive and checksum from [GitHub Releases](https://github.com/seanbrar/theater-mode/releases), verify the download against its checksum, then extract and install it:

```sh
sha256sum -c theater-mode-v*-linux-x86_64.tar.gz.sha256
tar xzf theater-mode-v*-linux-x86_64.tar.gz
cd theater-mode-v*-linux-x86_64
./install.sh
```

Every release is also signed by the workflow that built it. If you have the
[GitHub CLI](https://cli.github.com), verify the archive's provenance:

```sh
gh attestation verify theater-mode-v*-linux-x86_64.tar.gz \
    --repo seanbrar/theater-mode \
    --signer-workflow seanbrar/theater-mode/.github/workflows/release.yml
```

The one-command installer runs this check whenever `gh` is installed and signed in.
Without `gh`, it verifies the checksum alone.

</details>

## Change the appearance

The built-in settings are ready to use. These are the most common changes:

```sh
# Make the other monitors a little brighter (0 is unchanged; 1 is pitch black)
theater-mode config set effect.dimming 0.75

# Keep windows on the other monitors visible, with artwork behind them
# Pair this with lower dimming (such as 0.35) so artwork stays bright and clear
theater-mode config set effect.placement behind_windows
theater-mode config set effect.dimming 0.35

# Use a plain dark screen instead of Steam artwork
theater-mode config set effect.art false

# Wait until the game enters fullscreen before activating
theater-mode config set daemon.require_fullscreen true
```

Changes take effect immediately. To see every current setting, run:

```sh
theater-mode config show
```

The [reference configuration](config.reference.toml) lists every available setting and
its allowed values.

<details>
<summary>Use different settings on different monitors</summary>

First, find your monitor names and suggested headings:

```sh
theater-mode outputs
```

Set options for a specific monitor using `theater-mode config set`:

```sh
# Set dimming for a specific monitor by EDID or connector
theater-mode config set 'outputs."Dell Inc.:DELL S2721QS:4QCPZY3".dimming' 0.50

# Keep a specific secondary monitor bright with windows visible
theater-mode config set outputs.DP-2.placement behind_windows

# Leave one monitor out of theater mode entirely (never dim, never show artwork)
theater-mode config set outputs.DP-2.dimming 0
```

You can also edit `~/.config/theater-mode/config.toml` directly:

```toml
[outputs."Dell Inc.:DELL S2721QS:4QCPZY3"]
dimming = 0.50
placement = "behind_windows"
art = false
```

The manufacturer, model, and serial number let settings follow the physical monitor
if you move its cable to another port. If a monitor does not provide that information,
use its connector name (such as `DP-2`).

Per-monitor settings can change `placement`, `dimming`, `art`, `duration`, and `curve`.

</details>

## If something goes wrong

Not sure which applies? Run `theater-mode doctor`. It checks everything theater-mode
depends on and tells you what to do about whatever it finds.

**A monitor stays dim after the game closes**

Stop theater mode and begin restoring every monitor:

```sh
theater-mode clear
```

**The monitors dim, but there is no artwork**

Theater-mode uses artwork already downloaded by Steam. Open the game in your Steam
library once and try again. Native and Flatpak Steam are both detected.

**The `theater-mode` command is not found**

Close your terminal, open a new one, and retry. If it is still missing, run the command using its
full path: `~/.local/bin/theater-mode doctor`.

**What `theater-mode doctor` checks**

```sh
theater-mode doctor
```

Your session type and Plasma version, both helper programs, the KWin script and whether it
is switched on, the background service, your configuration, your displays, and the Steam
artwork cache. Home directory paths are shortened to `~`, so the output is safe to paste
into a bug report.

**I still need help**

Include the output of `theater-mode doctor` when
[opening a GitHub issue](https://github.com/seanbrar/theater-mode/issues), along with the
game that caused the problem. If the daemon is running but misbehaving, this adds detail:

```sh
journalctl --user -u theater-mode.service -b --no-pager
```

Unlike `doctor`, that output is not filtered, so glance over it for usernames or paths you
would rather not share publicly.

## Update or remove it

Update to the newest release:

```sh
theater-mode update
```

Remove theater-mode:

```sh
theater-mode uninstall
```

Both commands keep your settings. Uninstalling shows exactly what it will remove and asks
before doing it.

## How monitor selection works

KDE tells theater-mode when a Steam game window opens, closes, enters fullscreen, or moves
to another monitor. The monitor containing the game is left alone. Every other connected
monitor receives the effect.

You normally do not need to identify or arrange monitors yourself. Monitor names matter
only when you want different settings on different screens; `theater-mode outputs` prints
the exact names to use.

The effect is a removable image drawn over the desktop. It does not change monitor
brightness controls, HDR settings, or DDC/CI state. If the daemon stops unexpectedly, the
overlay helper exits and removes its surfaces as well.

Games running through Proton and games inside a nested Gamescope window are supported.
Bazzite and SteamOS Game Mode are different: there is no KDE desktop there for
theater-mode to watch, so Game Mode itself is not supported.

## Installing from source

A source install needs Python 3.12 or newer, a C compiler, `make`, `pkg-config`, and the
libwayland development headers.

```sh
git clone https://github.com/seanbrar/theater-mode
cd theater-mode
./install.sh
```

On an atomic desktop, build the native helpers in Distrobox and run the installer from the
host. The exact workflow is in [CONTRIBUTING.md](CONTRIBUTING.md#live-desktop-testing).

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for the development environment, tests, live
desktop workflow, and contribution guidelines.

## License

MIT. See [LICENSE](LICENSE) for details.
