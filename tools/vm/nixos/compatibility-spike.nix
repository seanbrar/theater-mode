{ pkgs ? import <nixpkgs> { }, releaseTarball ? null }:

assert releaseTarball != null;

let
  archive = builtins.path {
    path = releaseTarball;
    name = "theater-mode-release.tar.gz";
  };
in
pkgs.testers.runNixOSTest {
  name = "theater-mode-compatibility-spike";

  nodes.machine = { pkgs, ... }: {
    services.xserver.enable = true;
    services.displayManager.sddm.enable = true;
    services.desktopManager.plasma6.enable = true;
    services.displayManager.autoLogin.enable = true;
    services.displayManager.autoLogin.user = "tester";

    users.users.tester = {
      isNormalUser = true;
      password = "";
    };

    environment.systemPackages = with pkgs; [
      gnutar
      gzip
    ];
    environment.etc."theater-mode-release.tar.gz".source = archive;
  };

  testScript = ''
    machine.start()
    machine.wait_for_unit("display-manager.service")
    machine.succeed("install -d /tmp/theater-mode-release")
    machine.succeed(
      "tar xzf /etc/theater-mode-release.tar.gz "
      "--strip-components=1 -C /tmp/theater-mode-release"
    )
    machine.succeed("/tmp/theater-mode-release/bin/theater-dimmer --version")
    machine.succeed("/tmp/theater-mode-release/bin/theater-art --version")
  '';
}
