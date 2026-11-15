{
  description = "Page-aware benchmarking framework for file metadata index structures (CSE 510)";

  # flake.lock pins this to one nixpkgs commit, so everyone gets the same
  # Python and matplotlib. `nix flake update` moves the pin.
  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-26.05";

  outputs = { self, nixpkgs }:
    let
      systems = [ "x86_64-linux" "aarch64-linux" "x86_64-darwin" "aarch64-darwin" ];
      forAll = f: nixpkgs.lib.genAttrs systems (system: f nixpkgs.legacyPackages.${system});

      # The library needs only the standard library; matplotlib draws the
      # benchmark figures and is skipped when missing.
      python = pkgs: pkgs.python312;
      tools = pkgs: ps: [ ps.matplotlib ];

      dbmsi = pkgs: (python pkgs).pkgs.buildPythonPackage {
        pname = "dbmsi";
        version = "0.1.0";
        pyproject = true;
        src = pkgs.lib.cleanSource ./.;
        build-system = [ (python pkgs).pkgs.setuptools ];
        dependencies = tools pkgs (python pkgs).pkgs;
        pythonImportsCheck = [ "dbmsi" "dbmsi.bench" ];
      };
    in
    {
      packages = forAll (pkgs: { default = dbmsi pkgs; });

      # nix develop: the interpreter the benchmarks were run with
      devShells = forAll (pkgs: {
        default = pkgs.mkShell {
          packages = [ ((python pkgs).withPackages (tools pkgs)) ];
          # Fixed string hashing, so set iteration order matches across runs
          PYTHONHASHSEED = "0";
        };
      });

      # nix flake check: the full unit test suite
      checks = forAll (pkgs: {
        tests = pkgs.runCommand "dbmsi-tests" {
          nativeBuildInputs = [ ((python pkgs).withPackages (tools pkgs)) ];
          PYTHONHASHSEED = "0";
          MPLBACKEND = "Agg";
        } ''
          cp -r ${pkgs.lib.cleanSource ./.} src && chmod -R u+w src && cd src
          python3 -m unittest discover -s tests -t .
          touch $out
        '';
      });
    };
}
