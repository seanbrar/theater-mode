# Changelog

What changed in each release, written for the people who use theater-mode. Update to the
newest release with `theater-mode update`.

## 0.1.0

The first stable release. When you launch a Steam game, theater-mode dims your other
monitors and fills them with the game's Steam artwork. Move the game to another monitor
and the effect follows it. Close the game and your monitors return to normal.

The effect is an overlay drawn over the desktop. It does not change monitor brightness
settings, HDR calibration, refresh rates, or DDC/CI controls. If the background service
stops, the overlay exits and removes itself immediately. If a screen ever stays dim,
`theater-mode clear` restores every monitor.
