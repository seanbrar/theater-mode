# tools/vm/nixos — NixOS reproducibility control

A pinned NixOS Plasma guest, intended as the fixed reference point for `tools/vm`.

The Arch guest is built from mutable inputs, so a failure there does not say whether
theater-mode regressed, an upstream package regressed, or the base image changed. A guest
whose inputs never move narrows those cases: if the control passes and Arch fails, the
failure is specific to the Arch environment or theater-mode's interaction with it.

This is a non-gating developer tool. It is not part of `./bin/check` and does not declare
NixOS support.

---

## Current status: not yet implemented

The expression stands up SDDM, Plasma 6, and autologin declaratively, and that part is
sound. Two things are not.

It imports `<nixpkgs>`, so the caller's channel decides the system and the result is not
reproducible — which is the one property the control exists to provide.

Its assertions also target the wrong artifact. It unpacks a release archive and runs the
bundled helpers, and prebuilt binaries name an FHS dynamic linker that stock NixOS does not
provide, so those assertions cannot pass. Building in place avoids that boundary entirely:
`install.sh --build` compiles the helpers against the store's own glibc and wayland, and
the install then completes and runs. Redirecting the expression at a source install is what
turns this directory into the control described above.

---

## Running it

Requires Nix, `/dev/kvm`, and `<nixpkgs>` on the Nix search path.

Build both native helpers in the normal development environment, assemble a release
archive, and run the expression:

```sh
# 1. Build a local release archive from this checkout
./bin/make-release \
    --dimmer-bin src/theater_mode/dimmer/theater-dimmer \
    --art-bin src/theater_mode/art/theater-art

# 2. Boot the declarative NixOS VM against that archive
version="$(sed -n 's/^__version__ = "\(.*\)"/\1/p' src/theater_mode/__init__.py)"
archive="./dist/theater-mode-v${version}-linux-$(uname -m).tar.gz"
nix-build tools/vm/nixos/compatibility-spike.nix \
    --arg releaseTarball "$archive"
```

A failure while executing the helpers is the expected result until the target is redirected.

---

## Before this becomes a gate

- Pin one supported NixOS release by immutable revision and hash, not `<nixpkgs>`, and set
  a cadence for moving the pin.
- Take the source tree as input and install through `install.sh --build`, replacing the
  `releaseTarball` contract.
- Assert success rather than an expected failure.
- Cover the user unit, KWin script activation, daemon contact, and one effect transition.
