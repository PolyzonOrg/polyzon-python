"""Proxies and custom certificates.

* ``proxy=`` takes ``http://``, ``https://`` and (with ``pip install "polyzon[socks]"``)
  ``socks5://`` URLs; credentials in the URL are sent as Basic proxy auth.
* Without ``proxy=`` the ``HTTPS_PROXY`` / ``HTTP_PROXY`` / ``NO_PROXY`` environment
  is honoured; ``trust_env=False`` ignores it.
* HTTPS traffic is tunnelled with CONNECT: the proxy never sees your token.
* A corporate CA bundle goes through ``SSL_CERT_FILE`` or your own ``httpx.Client``.
"""

import os
import ssl

import httpx

from polyzon import Polyzon


def main() -> None:
    proxy_url = os.environ.get("MY_PROXY", "http://user:password@proxy.internal:3128")
    with Polyzon(proxy=proxy_url) as client:
        print(client.limits().per_minute)

    with Polyzon(trust_env=False) as direct:
        print(direct.me().account.id)

    ca_file = os.environ.get("MY_CA_BUNDLE")
    if ca_file:
        context = ssl.create_default_context(cafile=ca_file)
        with (
            httpx.Client(verify=context, proxy=proxy_url) as http,
            Polyzon(http_client=http) as custom,
        ):
            print(custom.me().token.prefix)


if __name__ == "__main__":
    main()
