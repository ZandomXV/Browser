"""
ssl_helper.py — Shared SSL fix for Python 3.14 + intercepting proxy.
Creates a requests Session that bypasses broken certificate chain validation.
"""
import ssl
import urllib3
import requests
from requests.adapters import HTTPAdapter
from urllib3.poolmanager import PoolManager

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


class NoSSLAdapter(HTTPAdapter):
    """HTTPS adapter that completely skips SSL certificate verification."""
    def init_poolmanager(self, connections, maxsize, block=False, **kwargs):
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        self.poolmanager = PoolManager(
            num_pools=connections,
            maxsize=maxsize,
            block=block,
            ssl_context=ctx,
        )


def create_session():
    """Create a requests Session with SSL verification disabled."""
    s = requests.Session()
    s.mount("https://", NoSSLAdapter())
    return s
