"""Outbound HTTPS must be able to verify certificates in a frozen build.

A PyInstaller one-file bundle carries no system trust store, so the station
reported "ingestion failed: network: ... SSLCertVerificationError: unable to
get local issuer certificate" for every feed, ingested zero articles, and
aired nothing — while the window was up and /health was green. These tests
pin the fix so a build cannot silently lose its CA bundle again.
"""

import os
import ssl
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def test_ca_bundle_is_found_and_contains_certificates():
    from core.tls import ca_bundle_path
    bundle = ca_bundle_path()
    assert bundle is not None, "no CA bundle found; HTTPS cannot be verified"
    text = bundle.read_text(errors="replace")
    assert "BEGIN CERTIFICATE" in text
    # A real bundle is not a stub.
    assert text.count("BEGIN CERTIFICATE") > 50


def test_install_ca_bundle_points_openssl_at_it(monkeypatch):
    from core.tls import install_ca_bundle
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    monkeypatch.delenv("SSL_CERT_DIR", raising=False)
    bundle = install_ca_bundle()
    assert bundle is not None
    assert os.environ["SSL_CERT_FILE"] == str(bundle)
    assert Path(os.environ["SSL_CERT_DIR"]).is_dir()


def test_install_never_disables_verification(monkeypatch):
    """Fail closed: the fix must not 'solve' SSL by turning it off."""
    from core.tls import default_ssl_context
    ctx = default_ssl_context()
    assert ctx.verify_mode == ssl.CERT_REQUIRED
    assert ctx.check_hostname is True


def test_importing_core_installs_the_bundle(monkeypatch):
    """The side effect must fire on `import core`, before any SSL context."""
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    monkeypatch.delenv("SSL_CERT_DIR", raising=False)
    code = (
        "import sys; sys.path.insert(0, %r);"
        "import core, os;"
        "print(os.environ.get('SSL_CERT_FILE') or '')" % str(ROOT)
    )
    out = subprocess.run([sys.executable, "-c", code],
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip(), "import core did not set SSL_CERT_FILE"


@pytest.mark.network
@pytest.mark.skipif(
    os.environ.get("MINDBROADCAST_RUN_NETWORK_TESTS") != "1",
    reason="network tests are opt-in; set MINDBROADCAST_RUN_NETWORK_TESTS=1 to run",
)
def test_real_https_fetch_verifies_certificates():
    """The transport must complete a real TLS handshake against a real host.

    This is the failure the packaged app hit; a stubbed session would pass
    while the shipped binary still could not reach the network.
    """
    import asyncio

    from core.ingestion.transport import AiohttpTransport

    async def fetch():
        transport = AiohttpTransport()
        return await transport.request(
            "https://www.example.com/",
            connect_timeout=15, read_timeout=25, checkpoint=None)

    result = asyncio.run(fetch())
    assert result["status_code"] == 200
    assert len(result["body"]) > 0


def test_release_script_bundles_and_checks_the_ca_bundle():
    """The build must collect certifi's data and fail closed without it."""
    script = (ROOT / "packaging" / "release.sh").read_text()
    assert '--collect-data "certifi"' in script, (
        "release.sh does not bundle certifi's cacert.pem; the frozen app "
        "would fail every HTTPS fetch")
    # The check must read the archive TOC. Grepping the binary is a false
    # negative: a one-file build compresses its data, so the certificate text
    # is not searchable in the file.
    assert "pyi-archive_viewer" in script, (
        "release.sh does not verify the CA bundle made it into the build")
    assert "certifi/cacert.pem" in script
    assert 'grep -qa "BEGIN CERTIFICATE"' not in script, (
        "release.sh greps the compressed binary for certificate text, which "
        "always fails and would block a good build")


def test_requirements_declare_certifi():
    reqs = (ROOT / "requirements.txt").read_text()
    assert "certifi" in reqs, (
        "certifi is not declared; a clean install and the PyInstaller build "
        "would have no CA bundle")
