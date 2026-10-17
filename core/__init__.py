"""MindBroadcast core package.

Importing anything under `core` installs the CA trust store as a side effect.
This is deliberate: outbound HTTPS (ingestion feeds, provider health checks)
must be able to verify certificates in a frozen build, and the bundle has no
system trust store. Doing it here covers every entry path — `python -m
core.desktop`, the PyInstaller wrapper, and `uvicorn core.inference.console:app`
— before any SSL context is created.
"""

from . import tls as _tls

_tls.install_ca_bundle()
