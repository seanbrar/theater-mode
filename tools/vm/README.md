# tools/vm — Arch Linux guest with virtual displays

Verifies that the current checkout installs, runs, and applies its effect cleanly on an
unmodified Arch Linux Plasma system. It provides a foreign glibc, an independently packaged
Plasma desktop, a real SDDM session, a real systemd user instance, and a pristine filesystem
that has never had theater-mode installed.

---

## Setup and workflows

All VM commands run inside the dedicated `theater-mode-vm` Distrobox container:

```sh
# 1. Create the VM tooling container (one-time)
distrobox assemble create --file tools/vm/distrobox.ini

# 2. Build native helpers in the development container
distrobox enter theater-mode-dev -- sh -c \
    'make -C src/theater_mode/dimmer && make -C src/theater_mode/art'

# 3. Provision the golden Arch image (downloads cloud image and installs Plasma; one-time)
distrobox enter theater-mode-vm -- tools/vm/vm.sh build

# Before a release: boot headless, install the checkout, and assert the effect
distrobox enter theater-mode-vm -- tools/vm/vm.sh check

# For manual inspection: launch a graphical session with two virtual displays
distrobox enter theater-mode-vm -- tools/vm/vm.sh run

# To diagnose provisioning or boot failures: boot with a serial console
distrobox enter theater-mode-vm -- tools/vm/vm.sh console
```

---

## What `tools/vm/vm.sh check` validates

The automated check boots the golden image on an ephemeral overlay and verifies:

* `install.sh` succeeds on a fresh Arch installation without host path assumptions.
* Host-compiled `theater-dimmer` and `theater-art` binaries execute against the guest's glibc without symbol faults.
* The KWin detection script is installed, enabled, and contacts the daemon after the
  installer reconfigures the desktop.
* The `theater-mode.service` systemd user unit activates cleanly under a live `graphical-session.target`.
* `theater-mode doctor` reports no blocking problems.
* The dimmer helper successfully dims secondary virtual DRM outputs during simulated game focus and restores them on clear.

---

## SSH and guest access

Forward guest SSH to the host to run custom diagnostic commands:

```sh
distrobox enter theater-mode-vm -- \
    env THEATER_VM_SSH_PORT=2222 tools/vm/vm.sh run

# From another terminal:
ssh -i ~/.local/share/theater-mode/vm/id_ed25519 -p 2222 tester@127.0.0.1
```

Inside the guest, run `theater-vm-check` as the `tester` user to re-run the full installation
and doctor check manually.

---

## Environment variables

| Variable | Default | Purpose |
| --- | --- | --- |
| `THEATER_VM_OUTPUTS` | `2` | Number of virtual DRM displays exposed to the guest. |
| `THEATER_VM_SSH_PORT` | *none* | Localhost port forwarded to guest SSH (port 22). The unattended `check` chooses a free port when this is unset. |
| `THEATER_VM_MEMORY` | `4096` | Guest RAM in MiB. |
| `THEATER_VM_CPUS` | `4` | Number of virtual CPU cores. |
| `THEATER_VM_STATE_DIR` | `~/.local/share/theater-mode/vm` | Directory storing base and golden qcow2 images. |
| `THEATER_VM_BUILD_TIMEOUT` | `1200` | Golden image build deadline in seconds. |
| `THEATER_VM_CHECK_TIMEOUT` | `300` | Unattended check readiness deadline in seconds. |

---

## Image maintenance

* **Inspect cached images:**
  ```sh
  distrobox enter theater-mode-vm -- tools/vm/vm.sh inspect
  ```
  Verifies the SHA256 checksum of the base image and validates the golden image backing chain.

* **Rebuild or clean:**
  ```sh
  distrobox enter theater-mode-vm -- tools/vm/vm.sh clean
  distrobox enter theater-mode-vm -- tools/vm/vm.sh build
  ```
  `clean` deletes base, golden, and temporary overlay images from `THEATER_VM_STATE_DIR`.
  Follow it with `build` when you intentionally want to download the current Arch cloud
  image and reprovision Plasma; ordinary runs continue using the cached golden image.
