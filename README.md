# theater-mode

When you launch a Steam game, theater-mode dims your other monitors and fills them with
the game's Steam artwork. Move the game to another monitor and the effect follows it.
Close the game and everything returns to normal.

## Will it work on my system?

You need all of these:

- two or more monitors;
- KDE Plasma 6.2 or newer;
- a Wayland desktop session; and
- games launched through Steam.

On Bazzite, use the **KDE edition in Desktop Mode**. The GNOME edition and Game Mode are
not supported. On SteamOS, switch to **Desktop Mode**; SteamOS versions before 3.8 use an
older desktop setup that is not supported.

The installer checks your desktop and stops with an explanation if it is not compatible.

## Install

Open a terminal, paste this command, and press Enter:

```sh
curl -fsSL https://raw.githubusercontent.com/seanbrar/theater-mode/main/get.sh | bash
```

The installer downloads the latest release, verifies the download, and starts
theater-mode. It installs only for your user account and does not need `sudo` or change
the operating system.

Now launch a Steam game. The other monitors should fade after its window appears.

The ready-made download is currently for x86_64 systems, including ordinary desktop PCs,
Steam Deck, and most Bazzite devices. See [Installing from source](#installing-from-source)
for other processors.

<details>
<summary>Install without piping a script into Bash, or verify a download yourself</summary>

Download the archive and checksum from [GitHub Releases](https://github.com/seanbrar/theater-mode/releases), verify the download against its checksum, then extract and install it:

```sh
sha256sum -c theater-mode-v*-linux-x86_64.tar.gz.sha256
tar xzf theater-mode-v*-linux-x86_64.tar.gz
cd theater-mode-v*-linux-x86_64
./install.sh
```

The checksum is published beside the archive, so on its own it only catches a damaged
download. Every release is also signed by the workflow that built it. With the
[GitHub CLI](https://cli.github.com), you can confirm the archive really is the one
GitHub built from this repository:

```sh
gh attestation verify theater-mode-v*-linux-x86_64.tar.gz \
    --repo seanbrar/theater-mode \
    --signer-workflow seanbrar/theater-mode/.github/workflows/release.yml
```

The one-command installer runs this same check whenever `gh` is installed and signed in,
and stops rather than installing an archive that fails it. Without `gh` it verifies the
checksum alone. `theater-mode update` also verifies the checksum only, because it cannot
assume `gh` is present.

</details>

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

## Change the appearance

The built-in settings are ready to use. These are the most common changes:

```sh
# Make the other monitors a little brighter. 0 is unchanged; 1 is completely black.
theater-mode config set effect.dimming 0.75

# Keep windows on the other monitors visible, with artwork behind them.
# Pair this with a lower dimming (such as 0.35) so the artwork stays bright and clear.
theater-mode config set effect.placement behind_windows
theater-mode config set effect.dimming 0.35

# Use a plain dark screen instead of Steam artwork.
theater-mode config set effect.art false

# Wait until the game enters fullscreen before activating.
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

First, ask theater-mode how it identifies your monitors:

```sh
theater-mode outputs
```

Open `~/.config/theater-mode/config.toml` and copy the suggested heading for the monitor
you want to change. Add settings below it, for example:

```toml
[outputs."Dell Inc.:DELL S2721QS:4QCPZY3"]
dimming = 0.50
placement = "behind_windows"
art = false
```

The manufacturer, model, and serial number let the setting follow the physical monitor
if you move its cable to another port. If a monitor does not provide that information,
use the suggested connector heading such as `[outputs.DP-2]` instead.

Per-monitor settings can change `placement`, `dimming`, `art`, `duration`, and `curve`.

To leave one monitor out of theater mode entirely, give it `dimming = 0`. That monitor is
never dimmed and never shows artwork, whatever the other settings say.

</details>

## If something goes wrong

Start here. This checks everything theater-mode depends on and tells you what to do about
whatever it finds:

```sh
theater-mode doctor
```

It covers your session type and Plasma version, both helper programs, the KWin script and
whether it is switched on, the background service, your configuration, your displays, and
the Steam artwork cache. Home directory paths are shortened to `~`, so the output is safe
to paste into a bug report.

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

**I still need help**

Include the output of `theater-mode doctor` when
[opening a GitHub issue](https://github.com/seanbrar/theater-mode/issues), along with the
game that caused the problem. If the daemon is running but misbehaving, this adds detail:

```sh
journalctl --user -u theater-mode.service -b --no-pager
```

Unlike `doctor`, that output is not filtered, so glance over it for usernames or paths you
would rather not share publicly.

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
host. The exact workflow is in [CONTRIBUTING.md](CONTRIBUTING.md#live-plasma-testing).

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for the development environment, tests, live
desktop workflow, and contribution guidelines.

## License

MIT. See [LICENSE](LICENSE) for details.
