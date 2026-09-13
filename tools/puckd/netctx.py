"""One TLS context for the three HTTPS calls the agent makes (Drive's
about, the site's latest.json and the .uf2). The python.org interpreter
ships no CA bundle, so from a source checkout every urlopen fails with
CERTIFICATE_VERIFY_FAILED; certifi's bundle fixes that and is shipped in
the app. Falls back to the platform default when certifi is missing."""
from __future__ import annotations

import ssl


def ssl_context() -> ssl.SSLContext:
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:  # noqa: BLE001 -- no certifi: the platform's own store
        return ssl.create_default_context()
