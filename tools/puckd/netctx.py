"""TLS for everything the agent fetches over HTTPS.

The python.org interpreter ships no CA bundle, so from a source checkout every
urlopen fails with CERTIFICATE_VERIFY_FAILED; certifi's bundle fixes that and
is shipped in the app.

HOW the bundle is handed over matters, and was wrong until 1.0.7. Inside the
app certifi lives in python314.zip, so `certifi.where()` extracts cacert.pem
to a temporary file in $TMPDIR ONCE and returns that path for the rest of
the process's life (certifi/core.py, `_CACERT_PATH`). py2app's __boot__.py
sets SSL_CERT_FILE to `<Resources>/openssl.ca/no-such-file`, so the
platform's default store is empty. And macOS runs /usr/libexec/dirhelper
every night at 03:35 (com.apple.bsd.dirhelper) to clean $TMPDIR. The agent
runs for days. If that one extracted file goes, every HTTPS call the agent
makes fails verification for as long as the process lives, and the old
`except Exception: return ssl.create_default_context()` below turned that
into an empty trust store with nothing said. MEASURED 2026-09-24 against the
released 1.0.6 bundle by the update review: HTTP 200 before the file was
deleted, CERTIFICATE_VERIFY_FAILED after, 200 again with `cadata=`.
Whether dirhelper actually removed it on the rider's Mac is NOT measured.

Two fixes, because two different stacks read certificates:
  * ssl_context() (urllib: the site's manifests, the .uf2, the app update,
    Drive's about call) loads certifi's CONTENT from memory, never a path.
  * install_ca_bundle() writes a copy to PUCKD_HOME (Application Support,
    which nothing cleans) and points SSL_CERT_FILE / CURL_CA_BUNDLE /
    REQUESTS_CA_BUNDLE at it. curl_cffi -- garminconnect's HTTP stack --
    picks its CA file ONCE, at import (curl_cffi/curl.py DEFAULT_CACERT: those
    env vars first, then certifi.where()), so this must run before garmin.py
    imports garminconnect. garmin.py calls it right there.
"""
from __future__ import annotations

import os
import ssl
import sys
from pathlib import Path
from typing import Optional

CA_FILENAME = "cacert.pem"
_CA_ENV_VARS = ("SSL_CERT_FILE", "CURL_CA_BUNDLE", "REQUESTS_CA_BUNDLE")

_certifi_pem: "Optional[str]" = None


def _certifi_contents() -> "Optional[str]":
    """certifi's PEM text, read from the package itself (from the zip inside
    the app) -- no temporary file. Cached: it is immutable data."""
    global _certifi_pem
    if _certifi_pem is None:
        try:
            import certifi
            _certifi_pem = certifi.contents()
        except Exception:  # noqa: BLE001 -- no certifi: say so below
            return None
    return _certifi_pem


def ssl_context() -> ssl.SSLContext:
    pem = _certifi_contents()
    if pem:
        return ssl.create_default_context(cadata=pem)
    # No certifi at all (a bare source checkout). The platform store is the
    # only option -- and inside the app, install_ca_bundle() has pointed
    # SSL_CERT_FILE at a real file, so even this is not the empty store.
    print("netctx: certifi unavailable; using the platform CA store", file=sys.stderr)
    return ssl.create_default_context()


def _is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False)) and bool(os.environ.get("RESOURCEPATH"))


def _puckd_home() -> Path:
    override = os.environ.get("PUCKD_HOME")
    return (Path(override).expanduser() if override
            else Path.home() / "Library" / "Application Support" / "JumpHeight")


def install_ca_bundle(*, frozen: "Optional[bool]" = None,
                      home: "Optional[Path]" = None) -> "Optional[Path]":
    """Give every TLS library in the app a CA file macOS never deletes, and
    point the standard env vars at it. Returns the path, or None when it did
    nothing (not inside the app, or it could not write). Never raises.

    Only inside the app: from a source checkout certifi is an ordinary file
    in site-packages and where() already returns a stable path."""
    if frozen is None:
        frozen = _is_frozen()
    if not frozen:
        return None
    pem = _certifi_contents()
    if not pem:
        return None
    try:
        dest = (home or _puckd_home()) / CA_FILENAME
        dest.parent.mkdir(parents=True, exist_ok=True)
        if not dest.is_file() or dest.read_text(encoding="utf-8") != pem:
            tmp = dest.with_name(dest.name + ".tmp")
            tmp.write_text(pem, encoding="utf-8")
            tmp.replace(dest)
    except OSError as exc:
        print(f"netctx: could not write {CA_FILENAME}: {exc!r}", file=sys.stderr)
        return None
    for var in _CA_ENV_VARS:
        os.environ[var] = str(dest)
    return dest
