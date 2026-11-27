# zoekt-mcp repository security, secret prevention, and engineering standards policies for shac.

_CURL_PIPE_SHELL = r"curl\b[^|\n]*\|\s*(sudo\s+)?(ba|z)?sh\b"
_UNEXPLAINED_PRAGMA = r"#\s*(pragma:\s*no\s*cover|noqa(:[^\s-]+)?|type:\s*ignore(\[[^\]]+\])?)\s*$"
_UNITTEST_MOCK = r"\b(unittest\.mock|from\s+unittest\s+import\s+mock)\b"
_MAX_OLD_SPACE_SIZE = r"--max-old-space-size\b"
_DEPRECATED_ZOEKT_NIP_IO = r"136\.82\.105\.184"

_FORBIDDEN_FILES = {
    ".env": True,
    "id_ed25519": True,
    "id_rsa": True,
    "authorized_keys": True,
    "HERMES.md": True,
    ".hermes.md": True,
    "auth.json": True,
}

_STANDARDS_REQUIRED_HEADERS = [
    "# Engineering standards for agentic engineers",
    "## 1. Tests: near-100% deterministic coverage",
    "## 2. Linters are essential",
    "## 3. One command proves it",
    "## 4. Security gates: Trivy vulnerability, secret, and misconfiguration scanning",
    "## 5. Documentation is a Docusaurus site with a documented server",
    "## 6. Working rules for agents",
]

def no_secrets_or_env_files(ctx):
    """Ensure no live secrets, credentials, or .env files are tracked in git."""
    for path in ctx.scm.all_files(include_symlinks = True):
        base = path.rpartition("/")[2]
        if base in _FORBIDDEN_FILES or path.endswith(".pem") or path.endswith(".key"):
            ctx.emit.finding(
                level = "error",
                filepath = path,
                message = "Forbidden secret or credential file '%s' must never be tracked in git." % path,
            )

def no_tracked_beads(ctx):
    """Beads are local-only and must never be committed."""
    for path in ctx.scm.all_files(include_symlinks = True):
        if path.startswith(".beads/") or path.startswith(".beads."):
            ctx.emit.finding(
                level = "error",
                filepath = path,
                message = "Local beads repository (.beads/) must not be committed.",
            )

def no_unexplained_pragma_or_disable(ctx):
    """Every inline exclusion (`# pragma: no cover - reason`) requires a reason comment on the same line (STANDARDS.md)."""
    for path in ctx.scm.all_files(glob = "*.py"):
        raw = str(ctx.io.read_file(path))
        for idx, line in enumerate(raw.splitlines()):
            if ctx.re.match(_UNEXPLAINED_PRAGMA, line):
                ctx.emit.finding(
                    level = "error",
                    filepath = path,
                    line = idx + 1,
                    message = "Inline exclusion or lint disable requires a '- <reason>' comment on the same line (STANDARDS.md).",
                )

def no_unittest_mock_in_tests(ctx):
    """Tests must use pure dependency injection and fakes, never unittest.mock (STANDARDS.md Section 1)."""
    for path in ctx.scm.all_files(glob = "tests/**/*.py"):
        raw = str(ctx.io.read_file(path))
        for idx, line in enumerate(raw.splitlines()):
            if ctx.re.match(_UNITTEST_MOCK, line):
                ctx.emit.finding(
                    level = "error",
                    filepath = path,
                    line = idx + 1,
                    message = "Use injected fakes or respx instead of unittest.mock (STANDARDS.md).",
                )

def no_heap_limit_bandaids(ctx):
    """No `--max-old-space-size` workarounds in code or service units (STANDARDS.md)."""
    for path in ctx.scm.all_files(include_symlinks = True):
        if path.endswith(".md") or path.startswith("checks/"):
            continue
        raw = str(ctx.io.read_file(path))
        for idx, line in enumerate(raw.splitlines()):
            if ctx.re.match(_MAX_OLD_SPACE_SIZE, line):
                ctx.emit.finding(
                    level = "error",
                    filepath = path,
                    line = idx + 1,
                    message = "Do not inflate V8 heap limits with --max-old-space-size (STANDARDS.md).",
                )

def standards_doc_required(ctx):
    """Enforce presence and section completeness of STANDARDS.md."""
    all_files = {f: True for f in ctx.scm.all_files(include_symlinks = True)}
    if "STANDARDS.md" not in all_files:
        ctx.emit.finding(
            level = "error",
            message = "STANDARDS.md is missing from the repository.",
        )
        return
    raw = str(ctx.io.read_file("STANDARDS.md"))
    for header in _STANDARDS_REQUIRED_HEADERS:
        if raw.find(header) < 0:
            ctx.emit.finding(
                level = "error",
                filepath = "STANDARDS.md",
                message = "STANDARDS.md is missing required section '%s'." % header,
            )

def no_deprecated_nip_io_gateway(ctx):
    """Forbid retired public GKE Ingress IP (136.82.105.184.nip.io); use https://code.innodata.studio."""
    for path in ctx.scm.all_files(include_symlinks = True):
        if path.startswith("checks/"):
            continue
        raw = str(ctx.io.read_file(path))
        for idx, line in enumerate(raw.splitlines()):
            if ctx.re.match(_DEPRECATED_ZOEKT_NIP_IO, line):
                ctx.emit.finding(
                    level = "error",
                    filepath = path,
                    line = idx + 1,
                    message = "Retired public Zoekt ingress IP (136.82.105.184) is forbidden; use https://code.innodata.studio.",
                )

def register_policy_checks():
    for impl in [
        no_secrets_or_env_files,
        no_tracked_beads,
        no_unexplained_pragma_or_disable,
        no_unittest_mock_in_tests,
        no_heap_limit_bandaids,
        no_deprecated_nip_io_gateway,
        standards_doc_required,
    ]:
        shac.register_check(impl)
