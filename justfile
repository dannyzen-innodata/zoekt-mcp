# zoekt-mcp repository quality gates and development commands.
# Aligns with STANDARDS.md: format check, lint, strict typecheck, shac test/check,
# 2x randomized 100%-coverage pytest, and Docusaurus 3 docs build.

set shell := ["bash", "-uc"]

# Run full quality gate via Nix flake check (shac, shac-tests, python-tests 2x random 100% coverage, docs build)
check:
	nix flake check -L

# Format all files via shac and ruff
fmt:
	nix develop -c ruff format src tests
	nix develop -c shac fmt

# Check Python formatting
fmt-check:
	nix develop -c ruff format --check src tests

# Lint all files via shac and ruff
lint:
	nix develop -c ruff check src tests
	nix develop -c shac check --all

# Strict typechecking
typecheck:
	nix develop -c mypy --strict src tests/test_client_unit.py tests/test_server_shaping.py tests/test_integration.py

# Run Starlark unit tests for shac policy rules
shac-test:
	nix develop -c shac test

# Run test suite twice in random order enforcing 100% line and branch coverage
test:
	nix develop -c pytest -p randomly tests/test_client_unit.py tests/test_server_shaping.py --cov=zoekt_mcp --cov-branch --cov-report=term-missing --cov-fail-under=100
	nix develop -c pytest -p randomly tests/test_client_unit.py tests/test_server_shaping.py --cov=zoekt_mcp --cov-branch --cov-report=term-missing --cov-fail-under=100

# Run live E2E integration tests against the M365-authenticated Zoekt GKE gateway
test-e2e url="https://code.innodata.studio":
	ZOEKT_URL="{{url}}" nix develop -c pytest tests/test_integration.py -v

# Start Docusaurus live-reload dev server on 127.0.0.1:3000
docs-dev:
	cd docs && nix develop .. -c bash -c "npm ci && npx docusaurus start --host 127.0.0.1 --port 3000"

# Build and serve static Docusaurus site on 127.0.0.1:3000 via Nix
docs-serve:
	nix run .#docs-serve
