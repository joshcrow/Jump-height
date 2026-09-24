"""tools/puckd/netctx.py -- the agent's TLS must not depend on a temporary file.

Inside the app certifi lives in a zip, so certifi.where() extracts cacert.pem
into $TMPDIR once and returns that path for the process's life, while py2app
points SSL_CERT_FILE at a file that does not exist. A long-running agent whose
extracted file was cleaned lost HTTPS entirely (measured against the released
1.0.6 bundle, 2026-09-24). These tests pin the two fixes.
"""
from __future__ import annotations

import os
import ssl
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "tools"))

from puckd import netctx  # noqa: E402

try:
    import certifi  # noqa: F401
    HAVE_CERTIFI = True
except ImportError:
    HAVE_CERTIFI = False


def _ca_count(ctx: ssl.SSLContext) -> int:
    return ctx.cert_store_stats().get("x509_ca", 0)


@unittest.skipUnless(HAVE_CERTIFI, "certifi is a CI dependency; installed in build.yml")
class TheContextNeverReadsAPath(unittest.TestCase):
    def test_the_context_holds_certifi_s_authorities(self):
        self.assertGreater(_ca_count(netctx.ssl_context()), 100)

    def test_a_vanished_where_file_does_not_empty_the_store(self):
        """The exact failure: where() still returns the extracted path, and
        the file behind it is gone."""
        gone = str(Path(tempfile.gettempdir()) / "jh-deleted-by-dirhelper-cacert.pem")
        self.assertFalse(Path(gone).exists())
        with patch("certifi.where", return_value=gone), \
                patch.dict(os.environ, {"SSL_CERT_FILE": "/nonexistent/no-such-file"}):
            self.assertGreater(_ca_count(netctx.ssl_context()), 100)


@unittest.skipUnless(HAVE_CERTIFI, "certifi is a CI dependency; installed in build.yml")
class TheBundleIsInstalledWhereNothingCleans(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        env = {k: v for k, v in os.environ.items()}
        self.addCleanup(lambda: (os.environ.clear(), os.environ.update(env)))

    def test_outside_the_app_it_does_nothing(self):
        before = {v: os.environ.get(v) for v in netctx._CA_ENV_VARS}
        self.assertIsNone(netctx.install_ca_bundle(frozen=False, home=self.home))
        self.assertFalse((self.home / netctx.CA_FILENAME).exists())
        self.assertEqual(before, {v: os.environ.get(v) for v in netctx._CA_ENV_VARS})

    def test_inside_the_app_it_writes_certifi_and_points_every_stack_at_it(self):
        import certifi
        path = netctx.install_ca_bundle(frozen=True, home=self.home)
        self.assertEqual(path, self.home / netctx.CA_FILENAME)
        self.assertEqual(path.read_text(encoding="utf-8"), certifi.contents())
        for var in ("SSL_CERT_FILE", "CURL_CA_BUNDLE", "REQUESTS_CA_BUNDLE"):
            self.assertEqual(os.environ[var], str(path), var)
        # ...and the platform default context, which reads SSL_CERT_FILE,
        # is no longer the empty store py2app left behind.
        self.assertGreater(_ca_count(ssl.create_default_context()), 100)

    def test_a_deleted_copy_is_rewritten_at_the_next_start(self):
        path = netctx.install_ca_bundle(frozen=True, home=self.home)
        path.unlink()
        self.assertEqual(netctx.install_ca_bundle(frozen=True, home=self.home), path)
        self.assertTrue(path.is_file())

    def test_curl_cffi_would_choose_it(self):
        """curl_cffi picks its CA ONCE at import: SSL_CERT_FILE /
        CURL_CA_BUNDLE / REQUESTS_CA_BUNDLE if the file exists, else
        certifi.where(). Mirror that rule here rather than re-importing the
        library (its choice is cached in a module global)."""
        path = netctx.install_ca_bundle(frozen=True, home=self.home)
        chosen = next((os.environ[v] for v in ("SSL_CERT_FILE", "CURL_CA_BUNDLE",
                                                "REQUESTS_CA_BUNDLE")
                       if os.environ.get(v) and os.path.exists(os.environ[v])), None)
        self.assertEqual(chosen, str(path))


if __name__ == "__main__":
    unittest.main()
