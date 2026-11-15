# For nix-shell without flakes: the same pinned nixpkgs and shell as
# `nix develop`, read from flake.lock.
let
  lock = (builtins.fromJSON (builtins.readFile ./flake.lock)).nodes.nixpkgs.locked;
  nixpkgs = fetchTarball {
    url = "https://github.com/NixOS/nixpkgs/archive/${lock.rev}.tar.gz";
    sha256 = lock.narHash;
  };
  pkgs = import nixpkgs { };
in
pkgs.mkShell {
  packages = [ (pkgs.python312.withPackages (ps: [ ps.matplotlib ])) ];
  PYTHONHASHSEED = "0";
}
