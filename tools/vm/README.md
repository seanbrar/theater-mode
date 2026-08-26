# tools/vm — clean guest verification

Two guests live here:
* `vm.sh` builds an upstream Arch Linux + Plasma guest and serves as the pre-release check.
* `steamos.sh` verifies the checkout against the read-only root, A/B partition sets, and
  Plasma configuration that ship on the Steam Deck, booting Valve's published images
  unmodified.

Use these before a release to verify glibc ABI compatibility, SDDM autologin, Wayland protocols, and systemd service units on clean distributions.

---

## Arch Linux guest (`vm.sh`)

Tests that the current checkout installs, boots, and runs its effect cleanly inside a pristine Arch Linux guest with virtual displays.

### Quick run (after first-time setup below)

```sh
distrobox enter theater-mode-vm -- tools/vm/vm.sh check
```
*Boots the cached golden image headless, runs `install.sh`, tests the effect across 2 virtual displays, and asserts `theater-mode doctor` exits clean (about 15s).*

For interactive graphical testing in a QEMU window:
```sh
distrobox enter theater-mode-vm -- tools/vm/vm.sh run
```

### First-time setup

Run these once to build the VM environment:

```sh
# 1. Create the VM container
distrobox assemble create --file tools/vm/distrobox.ini

# 2. Build the C helpers
distrobox enter theater-mode-dev -- sh -c \
    'make -C src/theater_mode/dimmer && make -C src/theater_mode/art'

# 3. Provision the golden Arch image (downloads cloud image and installs Plasma)
distrobox enter theater-mode-vm -- tools/vm/vm.sh build
```

<details>
<summary>Arch guest SSH access & interactive debugging</summary>

Forward guest SSH to the host:
```sh
distrobox enter theater-mode-vm -- \
    env THEATER_VM_SSH_PORT=2222 tools/vm/vm.sh run

# From another terminal:
ssh -i ~/.local/share/theater-mode/vm/id_ed25519 -p 2222 tester@127.0.0.1
```

Inside the guest, run `theater-vm-check` to re-run the full verification manually.

To boot into serial console for bootloader/kernel diagnostics:
```sh
distrobox enter theater-mode-vm -- tools/vm/vm.sh console
```

</details>

<details>
<summary>Arch image maintenance & environment overrides</summary>

### Image maintenance
* **Inspect cached images:**
  ```sh
  distrobox enter theater-mode-vm -- tools/vm/vm.sh inspect
  ```
  Verifies the SHA256 checksum of the base image and validates the golden image backing chain.

* **Clean cache & rebuild:**
  ```sh
  distrobox enter theater-mode-vm -- tools/vm/vm.sh clean
  distrobox enter theater-mode-vm -- tools/vm/vm.sh build
  ```
  `clean` deletes base, golden, and temporary overlay images from `THEATER_VM_STATE_DIR`.
  Follow it with `build` when you intentionally want to download the current Arch cloud
  image and reprovision Plasma; ordinary runs continue using the cached golden image.

### Environment variables
| Variable | Default | Purpose |
| --- | --- | --- |
| `THEATER_VM_OUTPUTS` | `2` | Number of virtual DRM displays exposed to the guest |
| `THEATER_VM_SSH_PORT` | *none* | Localhost port forwarded to guest SSH (port 22). Unattended `check` picks a free port when unset |
| `THEATER_VM_MEMORY` | `4096` | Guest RAM in MiB |
| `THEATER_VM_CPUS` | `4` | Number of virtual CPU cores |
| `THEATER_VM_STATE_DIR` | `~/.local/share/theater-mode/vm` | Directory storing base and golden qcow2 images |
| `THEATER_VM_BUILD_TIMEOUT` | `1200` | Golden image build deadline in seconds |
| `THEATER_VM_CHECK_TIMEOUT` | `300` | Unattended check readiness deadline in seconds |

</details>

---

## SteamOS guest (`steamos.sh`)

Keeps Valve's published SteamOS image or an official recovery installation unchanged,
then checks the checkout against the Plasma environment and read-only filesystem that
SteamOS ships. Each ordinary launch creates and provisions a small qcow2 overlay, so the
next launch starts from the same base and firmware-variable image again.

```sh
distrobox enter theater-mode-vm -- tools/vm/steamos.sh check      # unattended verification of displays, install & doctor
distrobox enter theater-mode-vm -- tools/vm/steamos.sh run        # boot what was installed (with 2 virtual displays)
distrobox enter theater-mode-vm -- tools/vm/steamos.sh ssh        # shell into the running guest
distrobox enter theater-mode-vm -- tools/vm/steamos.sh builds     # list what Valve publishes
```

### First-time setup

Choose one of two ways to prepare the virtual drive:

1. **Unattended import (fastest):** Write a published SteamOS build directly to the drive:
   ```sh
   distrobox enter theater-mode-vm -- tools/vm/steamos.sh import stable
   ```
2. **Official recovery install:** Boot the repair desktop and run the official installer:
   ```sh
   distrobox enter theater-mode-vm -- tools/vm/steamos.sh install
   ```
   In the QEMU window, double-click **Wipe Device & Install SteamOS**, click **Proceed**, and shut down the guest when it completes.

Both methods leave the resulting drive unchanged. Before each launch, `steamos.sh`
direct-boots a small in-memory environment against the launch overlay to configure SDDM
for Plasma, grant passwordless sudo to `deck`, enable SSH, and install the guest SSH key.
This takes less than a second and writes only the changed configuration blocks to the
overlay.

### Where the images come from

Valve publishes raw disk images rather than ISOs:
* `.../recovery/` holds recovery repair images. `fetch` takes the newest release.
* `.../steamdeck/<build id>/` holds historical and current builds (~670 builds). `builds` lists them with branch and version, and `import` writes one straight to the drive.

An imported build carries a single root partition where the installer lays down A/B partition sets, so `steamos-update` works only on a drive created by `install`.

<details>
<summary>SteamOS maintenance & environment overrides</summary>

### Image maintenance
* **Inspect cached images:**
  ```sh
  distrobox enter theater-mode-vm -- tools/vm/steamos.sh inspect
  ```
* **Clean cache:**
  ```sh
  distrobox enter theater-mode-vm -- tools/vm/steamos.sh clean
  ```

### Environment variables
| Variable | Default | Purpose |
| --- | --- | --- |
| `THEATER_STEAMOS_OUTPUTS` | `2` | Number of virtual displays exposed to the guest |
| `THEATER_STEAMOS_SSH_PORT` | `2222` | Localhost port forwarded to guest SSH (`check` uses an ephemeral free port when unset) |
| `THEATER_STEAMOS_MEMORY` | `8192` | Guest RAM in MiB |
| `THEATER_STEAMOS_CPUS` | `4` | Number of virtual CPU cores |
| `THEATER_STEAMOS_DISK` | `64G` | Virtual drive capacity |
| `THEATER_STEAMOS_BUILD` | `stable` | Default build or branch for `import` |
| `THEATER_STEAMOS_VARIANT` | `steamdeck` | Hardware variant: `steamdeck` or `fremont` (generic PC) |
| `THEATER_STEAMOS_SETTLE` | `30` | Seconds to wait before `screenshot` captures framebuffer |
| `THEATER_STEAMOS_PERSIST` | `0` | `1` to retain guest changes in a persistent overlay; the base drive remains unchanged |
| `THEATER_STEAMOS_CHECK_TIMEOUT` | `300` | Readiness deadline for graphical session in seconds |
| `THEATER_STEAMOS_MACHINE` | `pc` | QEMU machine type: `pc` or `q35` |
| `THEATER_VM_STATE_DIR` | `~/.local/share/theater-mode/vm` | Cache root, shared with `vm.sh` |

</details>

### Technical Invariants & Quirks

* **Desktop session crash loop**: SteamOS defaults SDDM to `Session=gamescope-wayland.desktop` with `Relogin=true`. In QEMU, gamescope terminates immediately without hardware GPU virtualization, causing SDDM to relaunch it in an infinite loop. Each launch overlay overrides SDDM autologin with `/etc/sddm.conf.d/zz-steamos-autologin.conf` (`Session=plasma.desktop`). Provisioning leaves Btrfs roots read-only, and the overlay is discarded after an ordinary run.
* **Firmware boot priority**: OVMF firmware attempts PXE network boot over virtio-net unless the NVMe device has an explicit `bootindex=0`.
* **Multi-display virtual connectors**: SteamOS drives an emulated `VGA` adapter for the primary head and `secondary-vga` for secondary displays. DRM connectors (`card0-Virtual-1` and `card1-Virtual-2`) report `connected` under `-display none` without requiring an active virtual X server.
* **Toolchain & installation prerequisites**: Stock SteamOS ships with Python 3.13 and Plasma 6.4, but does not provide `gcc`, `make`, `pkg-config`, or Wayland client headers. Prebuilt release tarballs install cleanly on the read-only root without modifying filesystem protections. Testing directly from a git checkout requires passing host-built helpers via `--dimmer-bin` and `--art-bin`.
* **Writable installer overlay**: The recovery installer writes to its own root during execution. Attaching the repair image with `readonly=on` hangs after GRUB; it must be backed by a writable overlay.
