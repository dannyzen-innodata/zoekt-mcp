# Unit tests for checks/policy.star (`shac test`).

load(
    "//checks/policy.star",
    "no_deprecated_nip_io_gateway",
    "no_heap_limit_bandaids",
    "no_secrets_or_env_files",
    "no_tracked_beads",
    "no_unexplained_pragma_or_disable",
    "no_unittest_mock_in_tests",
    "standards_doc_required",
)

def test_no_tracked_beads():
    res = testing.run(no_tracked_beads, files = {
        ".beads/issues.jsonl": "{}\n",
        ".beads.lock": "",
        "README.md": "ok\n",
    })
    asserts.eq(sorted([f.filepath for f in res.findings]), [".beads.lock", ".beads/issues.jsonl"])

def test_no_secrets_or_env_files():
    res = testing.run(no_secrets_or_env_files, files = {
        ".env": "SECRET=1\n",
        "auth.json": "{}\n",
        "keys/id_ed25519": "priv\n",
        "tls/server.pem": "cert\n",
    })
    asserts.eq(
        sorted([f.filepath for f in res.findings]),
        [".env", "auth.json", "keys/id_ed25519", "tls/server.pem"],
    )

def test_no_unexplained_pragma_or_disable():
    res = testing.run(no_unexplained_pragma_or_disable, files = {
        "src/zoekt_mcp/bad.py": "if __name__ == '__main__':  # pragma: no cover\n",
        "src/zoekt_mcp/good.py": "if __name__ == '__main__':  # pragma: no cover - module entrypoint\n",
    })
    asserts.eq([(f.filepath, f.line) for f in res.findings], [("src/zoekt_mcp/bad.py", 1)])

def test_no_unittest_mock_in_tests():
    res = testing.run(no_unittest_mock_in_tests, files = {
        "tests/test_bad.py": "from unittest import mock\n",
        "tests/test_good.py": "import respx\n",
    })
    asserts.eq([(f.filepath, f.line) for f in res.findings], [("tests/test_bad.py", 1)])

def test_no_heap_limit_bandaids():
    flag = "--max-old-" + "space-size=8192"
    res = testing.run(no_heap_limit_bandaids, files = {
        "systemd/bad.service": "Environment=\"NODE_OPTIONS=" + flag + "\"\n",
        "README.md": "Never use " + flag + "\n",
    })
    asserts.eq([(f.filepath, f.line) for f in res.findings], [("systemd/bad.service", 1)])

def test_standards_doc_required():
    res = testing.run(standards_doc_required, files = {
        "STANDARDS.md": "# Engineering standards\n",
    })
    asserts.eq(len(res.findings), 7)

def test_no_deprecated_nip_io_gateway():
    old_ip = "136.82." + "105.184"
    res = testing.run(no_deprecated_nip_io_gateway, files = {
        "README.md": "https://" + old_ip + ".nip.io\n",
        "docs/content/intro.md": "https://code.innodata.studio\n",
        "checks/policy_test.star": old_ip + "\n",
    })
    asserts.eq([(f.filepath, f.line) for f in res.findings], [("README.md", 1)])
