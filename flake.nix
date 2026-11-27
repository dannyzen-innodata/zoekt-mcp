{
  description = "zoekt-mcp: M365-Authenticated Sourcegraph Zoekt MCP Server";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/0954f7ee2f6bb3dc7d4e3d0d8bcb8fd4bde4cfc5";
    shac-src = {
      url = "github:shac-project/shac/63c0571f70f71c533d5ce623fb30a4d8813f24af";
      flake = false;
    };
  };

  outputs =
    {
      self,
      nixpkgs,
      shac-src,
    }:
    let
      inherit (nixpkgs) lib;
      systems = [
        "x86_64-linux"
        "aarch64-linux"
      ];
      forAllSystems = f: lib.genAttrs systems (system: f nixpkgs.legacyPackages.${system});

      shacFor = pkgs: pkgs.callPackage ./nix/shac.nix { src = shac-src; };

      zoektMcpFor =
        pkgs:
        pkgs.python3Packages.buildPythonApplication {
          pname = "zoekt-mcp";
          version = "0.4.1";
          pyproject = true;
          src = lib.fileset.toSource {
            root = ./.;
            fileset = lib.fileset.unions [
              ./pyproject.toml
              ./README.md
              ./LICENSE
              ./src
            ];
          };
          build-system = [ pkgs.python3Packages.hatchling ];
          dependencies = with pkgs.python3Packages; [
            mcp
            httpx
            pydantic
          ];
          doCheck = false;
          meta = {
            description = "M365-Authenticated Sourcegraph Zoekt Code Search MCP Server";
            mainProgram = "zoekt-mcp";
          };
        };

      pythonFor =
        pkgs:
        pkgs.python3.withPackages (
          ps: with ps; [
            mcp
            httpx
            pydantic
            respx
            pytest
            pytest-asyncio
            pytest-cov
            pytest-randomly
            mypy
            (zoektMcpFor pkgs)
          ]
        );

      docsFor =
        pkgs:
        pkgs.stdenv.mkDerivation {
          pname = "zoekt-mcp-docs";
          version = "0.4.1";
          src = lib.fileset.toSource {
            root = ./.;
            fileset = lib.fileset.unions [
              ./docs
              ./STANDARDS.md
            ];
          };
          npmRoot = "docs";
          npmDeps = pkgs.fetchNpmDeps {
            src = ./docs;
            hash = "sha256-3gbrkEVmjuCoT1Kt4ZtGVBHQujT8xSqBGUplILrsfVs=";
          };
          nativeBuildInputs = [
            pkgs.nodejs_22
            pkgs.npmHooks.npmConfigHook
          ];
          buildPhase = ''
            runHook preBuild
            cd docs
            npm run build
            runHook postBuild
          '';
          installPhase = ''
            cp -r build $out
          '';
        };

      docsServeFor =
        pkgs:
        pkgs.writeShellApplication {
          name = "zoekt-mcp-docs-serve";
          runtimeInputs = [ pkgs.python3 ];
          text = ''
            port="''${PORT:-3000}"
            echo "zoekt-mcp docs: http://127.0.0.1:$port   (Ctrl-C to stop)"
            echo "from a laptop: gcloud compute ssh <vm> -- -N -L $port:localhost:$port"
            exec python3 -m http.server --bind 127.0.0.1 "$port" --directory ${docsFor pkgs}
          '';
        };

      devTools =
        pkgs: with pkgs; [
          (shacFor pkgs)
          (pythonFor pkgs)
          (zoektMcpFor pkgs)
          ruff
          nixfmt
          statix
          deadnix
          yamllint
          nodejs_22
          prettier
          just
          git
        ];

      mkCheck =
        pkgs: name: script:
        pkgs.runCommand "check-${name}"
          {
            nativeBuildInputs = devTools pkgs;
          }
          ''
            cp -r ${self} src
            chmod -R u+w src
            cd src
            export HOME="$TMPDIR/home" LANG=C.UTF-8 TZ=UTC
            mkdir -p "$HOME"
            ${script}
            touch $out
          '';
    in
    {
      packages = forAllSystems (pkgs: {
        default = zoektMcpFor pkgs;
        zoekt-mcp = zoektMcpFor pkgs;
        shac = shacFor pkgs;
        docs = docsFor pkgs;
      });

      apps = forAllSystems (pkgs: {
        docs-serve = {
          type = "app";
          program = "${docsServeFor pkgs}/bin/zoekt-mcp-docs-serve";
        };
      });

      devShells = forAllSystems (pkgs: {
        default = pkgs.mkShell {
          packages = devTools pkgs;
        };
      });

      formatter = forAllSystems (pkgs: pkgs.nixfmt);

      checks = forAllSystems (
        pkgs:
        {
          docs = docsFor pkgs;
          package = zoektMcpFor pkgs;
        }
        // lib.mapAttrs (mkCheck pkgs) {
          shac = ''
            shac check --all
          '';
          shac-tests = ''
            shac test
          '';
          python-tests = ''
            pytest -p randomly tests/test_client_unit.py tests/test_server_shaping.py --cov=zoekt_mcp --cov-branch --cov-report=term-missing --cov-fail-under=100
            pytest -p randomly tests/test_client_unit.py tests/test_server_shaping.py --cov=zoekt_mcp --cov-branch --cov-report=term-missing --cov-fail-under=100
          '';
        }
      );
    };
}
