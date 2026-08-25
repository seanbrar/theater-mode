# tools/vm — clean Arch Linux VM verification

Tests that the current checkout installs, boots, and runs its effect cleanly inside a pristine Arch Linux guest with virtual displays.

Use this before a release to verify glibc ABI compatibility, SDDM autologin, and systemd units on an independent distribution.

---

## Quick run (after first-time setup below)

```sh
distrobox enter theater-mode-vm -- tools/vm/vm.sh check
```
*Boots the cached golden image headless, runs `install.sh`, tests the effect across 2 virtual displays, and asserts `theater-mode doctor` exits clean (about 15s).*

For interactive graphical testing in a QEMU window:
```sh
distrobox enter theater-mode-vm -- tools/vm/vm.sh run
```

---

## First-time setup

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

---

<details>
<summary>Guest SSH access & interactive debugging</summary>

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
<summary>Image maintenance & environment overrides</summary>

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
| `THEATER_VM_SSH_PORT` | *none* | Localhost port forwarded to guest SSH (port 22). The unattended `check` picks a free port when this is unset |
| `THEATER_VM_MEMORY` | `4096` | Guest RAM in MiB |
| `THEATER_VM_CPUS` | `4` | Number of virtual CPU cores |
| `THEATER_VM_STATE_DIR` | `~/.local/share/theater-mode/vm` | Directory storing base and golden qcow2 images |
| `THEATER_VM_BUILD_TIMEOUT` | `1200` | Golden image build deadline in seconds |
| `THEATER_VM_CHECK_TIMEOUT` | `300` | Unattended check readiness deadline in seconds |

</details>
