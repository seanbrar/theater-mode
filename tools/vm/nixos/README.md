# NixOS compatibility spike

This directory contains a non-gating developer probe to evaluate whether an unmodified
release archive executes its bundled native helpers on a vanilla NixOS Plasma system.

It is not part of `./bin/check` and does not declare official NixOS package support. The
probe is currently a stub: it stops at executing the two native helpers and is expected
to fail there until NixOS compatibility is implemented.

---

## Running the spike

From a host system with Nix installed and `/dev/kvm` available, first build both native
helpers using the normal development environment. Then package those exact binaries and
pass the resulting archive to the probe:

```sh
# 1. Build a local release tarball from the helpers in this checkout
./bin/make-release \
    --dimmer-bin src/theater_mode/dimmer/theater-dimmer \
    --art-bin src/theater_mode/art/theater-art

# 2. Build and boot the declarative NixOS VM probe against the release tarball
version="$(sed -n 's/^__version__ = "\(.*\)"/\1/p' src/theater_mode/__init__.py)"
archive="./dist/theater-mode-v${version}-linux-$(uname -m).tar.gz"
nix-build tools/vm/nixos/compatibility-spike.nix \
    --arg releaseTarball "$archive"
```

A failure while executing `theater-dimmer --version` or `theater-art --version` is the
current expected result. This stub does not yet test installation, services, or an effect
inside the Plasma session.
