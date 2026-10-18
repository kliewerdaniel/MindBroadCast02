"""Make a CA trust store available to every outbound HTTPS call.

Why this exists
---------------
`aiohttp` (the ingestion transport) and `urllib` (the provider/detector
health checks) both build their default SSL context from the *system* trust
store. A PyInstaller one-file bundle does not carry one: the bundled Python
has no `cacert.pem`, and `openssl` cannot find the host's certificate
directory from inside the extraction dir. Every HTTPS request then dies with

    SSLCertVerificationError: unable to get local issuer certificate

which is fatal but silent in the product: the station reports "ingestion
failed: network: ..." per feed, ingests zero articles, and therefore airs
nothing. The window is up and /health is green, so nothing looks broken.

The fix is to ship `certifi`'s CA bundle inside the build and point OpenSSL at
it before any context is created. `ssl.create_default_context()` reads
``SSL_CERT_FILE`` and ``SSL_CERT_DIR`` when it loads its default certs, so
setting them at process start covers both `aiohttp` and `urllib` with no
per-call plumbing.

This module must be imported *before* the first SSL context is built, which is
why the entry points call it at the very top.
"""

import os
import ssl
from pathlib import Path


def ca_bundle_path() -> Path | None:
    """The CA bundle to trust, or None if none can be found.

    Prefers certifi (shipped with the build). Falls back to the platform's
    own bundle so a source run on a machine without certifi still works.
    """
    try:
        import certifi
        path = Path(certifi.where())
        if path.is_file():
            return path
    except Exception:
        pass

    # Platform fallbacks for a source run that somehow lacks certifi.
    for candidate in (
        Path("/etc/ssl/cert.pem"),            # macOS / LibreSSL
        Path("/etc/ssl/certs/ca-certificates.crt"),  # Debian/Ubuntu
        Path("/etc/pki/tls/certs/ca-bundle.crt"),    # RHEL/Fedora
        Path("/etc/ssl/ca-bundle.pem"),              # openSUSE
    ):
        if candidate.is_file():
            return candidate
    return None


def install_ca_bundle() -> Path | None:
    """Point OpenSSL at a real CA bundle. Idempotent; safe to call anywhere.

    Returns the bundle in use, or None when nothing was found — in which case
    the caller keeps the previous behaviour rather than pretending to be
    secure. Never disables verification.
    """
    bundle = ca_bundle_path()
    if bundle is None:
        return None

    # Respect an explicit operator override (a corporate MITM proxy, say).
    if not os.environ.get("SSL_CERT_FILE"):
        os.environ["SSL_CERT_FILE"] = str(bundle)
    if not os.environ.get("SSL_CERT_DIR"):
        # certifi ships a single file; point the dir at its parent so OpenSSL
        # has a valid directory even though it holds no hashed certs.
        os.environ["SSL_CERT_DIR"] = str(bundle.parent)

    return bundle


def default_ssl_context() -> ssl.SSLContext:
    """A verifying SSL context that trusts the bundled bundle.

    For callers that pass a context explicitly (aiohttp's ``ssl=``), so the
    trust store does not depend on the environment being read at the right
    moment.
    """
    bundle = ca_bundle_path()
    if bundle is not None:
        return ssl.create_default_context(cafile=str(bundle))
    return ssl.create_default_context()
