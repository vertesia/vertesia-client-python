import base64
import json
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

from vertesia_client import Client, ClientOptions, VertesiaClientError
from vertesia_client.openapi.models.complex_search_payload import ComplexSearchPayload


def jwt_with_exp(exp):
    header = base64.urlsafe_b64encode(json.dumps({"alg": "none"}).encode()).decode().rstrip("=")
    payload = base64.urlsafe_b64encode(json.dumps({"exp": exp}).encode()).decode().rstrip("=")
    return f"{header}.{payload}."


class TokenHandler(BaseHTTPRequestHandler):
    responses = []
    requests = []

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length).decode()
        self.__class__.requests.append(
            {
                "path": self.path,
                "authorization": self.headers.get("Authorization"),
                "api_version": self.headers.get("x-api-version"),
                "body": body,
            }
        )
        payload = self.__class__.responses.pop(0)
        data = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format, *args):
        return


class APIHandler(BaseHTTPRequestHandler):
    requests = []

    def do_GET(self):
        self._record()
        self._send_json(None)

    def do_POST(self):
        self._record()
        self._send_json(None)

    def _record(self):
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length).decode() if length else ""
        self.__class__.requests.append(
            {
                "method": self.command,
                "path": self.path,
                "authorization": self.headers.get("Authorization"),
                "api_version": self.headers.get("x-api-version"),
                "body": body,
            }
        )

    def _send_json(self, payload):
        data = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format, *args):
        return


class TokenServer:
    def __enter__(self):
        TokenHandler.requests = []
        TokenHandler.responses = []
        self.server = HTTPServer(("127.0.0.1", 0), TokenHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        return f"http://127.0.0.1:{self.server.server_port}"

    def __exit__(self, exc_type, exc, tb):
        self.server.shutdown()
        self.thread.join(timeout=5)
        self.server.server_close()


class APIServer:
    def __enter__(self):
        APIHandler.requests = []
        self.server = HTTPServer(("127.0.0.1", 0), APIHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        return f"http://127.0.0.1:{self.server.server_port}"

    def __exit__(self, exc_type, exc, tb):
        self.server.shutdown()
        self.thread.join(timeout=5)
        self.server.server_close()


class ClientTest(unittest.TestCase):
    def test_default_endpoint_resolution(self):
        client = Client(token="token")
        self.assertEqual(client.studio_url, "https://api.vertesia.io/api/v1")
        self.assertEqual(client.store_url, "https://api.vertesia.io/api/v1")
        self.assertEqual(client.token_server_url, "https://sts.vertesia.io")

    def test_region_and_preview_endpoint_resolution(self):
        client = Client(region="us1", preview=True, token="token")
        self.assertEqual(client.studio_url, "https://api-preview.us1.vertesia.io/api/v1")
        self.assertEqual(client.store_url, "https://api-preview.us1.vertesia.io/api/v1")
        self.assertEqual(client.token_server_url, "https://sts.us1.vertesia.io")

    def test_custom_split_endpoints(self):
        client = Client(
            server_url="http://localhost:8091",
            store_url="http://localhost:8092/base",
            token_server_url="http://localhost:8093",
            token="token",
        )
        self.assertEqual(client.studio_url, "http://localhost:8091/api/v1")
        self.assertEqual(client.store_url, "http://localhost:8092/base/api/v1")
        self.assertEqual(client.token_server_url, "http://localhost:8093")

    def test_global_preview_endpoint_resolution(self):
        client = Client(preview=True, token="token")
        self.assertEqual(client.studio_url, "https://api-preview.vertesia.io/api/v1")
        self.assertEqual(client.store_url, "https://api-preview.vertesia.io/api/v1")
        self.assertEqual(client.token_server_url, "https://sts.vertesia.io")

    def test_site_endpoint_resolution(self):
        client = Client(site="api.us1.vertesia.io", token="token")
        self.assertEqual(client.studio_url, "https://api.us1.vertesia.io/api/v1")
        self.assertEqual(client.store_url, "https://api.us1.vertesia.io/api/v1")
        self.assertEqual(client.token_server_url, "https://sts.us1.vertesia.io")

    def test_already_normalized_endpoints(self):
        client = Client(
            server_url="https://api.dev1.vertesia.io/api/v1",
            store_url="https://api.dev1.vertesia.io/api/v1",
            token_server_url="https://sts.dev1.vertesia.io/",
            token="token",
        )
        self.assertEqual(client.studio_url, "https://api.dev1.vertesia.io/api/v1")
        self.assertEqual(client.store_url, "https://api.dev1.vertesia.io/api/v1")
        self.assertEqual(client.token_server_url, "https://sts.dev1.vertesia.io")

    def test_custom_host_falls_back_to_default_sts(self):
        client = Client(
            server_url="https://studio-server-dev-main.example.com",
            store_url="https://zeno-server-dev-main.example.com",
            token="token",
        )
        self.assertEqual(client.studio_url, "https://studio-server-dev-main.example.com/api/v1")
        self.assertEqual(client.store_url, "https://zeno-server-dev-main.example.com/api/v1")
        self.assertEqual(client.token_server_url, "https://sts.vertesia.io")

    def test_requires_complete_split_urls(self):
        with self.assertRaisesRegex(VertesiaClientError, "site or store_url is required"):
            Client(server_url="http://localhost:8091")
        with self.assertRaisesRegex(VertesiaClientError, "site or server_url is required"):
            Client(store_url="http://localhost:8092")

    def test_rejects_ambiguous_endpoint_options(self):
        with self.assertRaisesRegex(VertesiaClientError, "either site or region"):
            Client(site="api.us1.vertesia.io", region="us1")
        with self.assertRaisesRegex(VertesiaClientError, "region must be a region id"):
            Client(region="api.us1.vertesia.io")

    def test_token_bypasses_sts(self):
        client = Client(token="issued-token")
        auth = client.accounts.api_client.configuration.auth_settings()
        self.assertEqual(auth["bearerAuth"]["value"], "Bearer issued-token")

    def test_api_key_exchanges_through_sts(self):
        with TokenServer() as url:
            TokenHandler.responses.append({"token": "issued-token", "token_type": "Bearer", "expires_in": 3600})
            client = Client(api_key="sk-secret", token_server_url=url)
            auth = client.accounts.api_client.configuration.auth_settings()

        self.assertEqual(auth["bearerAuth"]["value"], "Bearer issued-token")
        self.assertEqual(TokenHandler.requests[0]["path"], "/token/issue")
        self.assertEqual(TokenHandler.requests[0]["authorization"], "Bearer sk-secret")
        self.assertEqual(TokenHandler.requests[0]["api_version"], "20260319")
        self.assertEqual(json.loads(TokenHandler.requests[0]["body"]), {"type": "apikey", "key": "sk-secret"})

    def test_invalid_api_key_fails_before_network_access(self):
        with self.assertRaisesRegex(VertesiaClientError, "sk- secret key"):
            Client(api_key="invalid-key")

    def test_api_key_and_token_are_mutually_exclusive(self):
        with self.assertRaisesRegex(VertesiaClientError, "either api_key or token"):
            Client(api_key="sk-secret", token="token")

    def test_custom_endpoints_with_api_key_require_token_server_url(self):
        with self.assertRaisesRegex(VertesiaClientError, "token_server_url is required"):
            Client(server_url="http://localhost:8091", store_url="http://localhost:8092", api_key="sk-secret")

    def test_near_expired_jwt_refreshes(self):
        with TokenServer() as url:
            TokenHandler.responses.extend(
                [
                    {"token": jwt_with_exp(time.time() + 10), "token_type": "Bearer", "expires_in": 3600},
                    {"token": "second-token", "token_type": "Bearer", "expires_in": 3600},
                ]
            )
            client = Client(api_key="sk-secret", token_server_url=url)
            first = client.accounts.api_client.configuration.auth_settings()["bearerAuth"]["value"]
            second = client.objects.api_client.configuration.auth_settings()["bearerAuth"]["value"]

        self.assertNotEqual(first, second)
        self.assertEqual(second, "Bearer second-token")
        self.assertEqual(len(TokenHandler.requests), 2)

    def test_generated_studio_and_store_clients_share_current_bearer_token(self):
        client = Client(token="shared-token")
        studio_auth = client.studio.api_client.configuration.auth_settings()
        store_auth = client.store.api_client.configuration.auth_settings()
        self.assertEqual(studio_auth["bearerAuth"]["value"], "Bearer shared-token")
        self.assertEqual(store_auth["bearerAuth"]["value"], "Bearer shared-token")

    def test_generated_requests_receive_version_and_aliases(self):
        with APIServer() as api_url:
            client = Client(
                server_url=api_url,
                store_url=api_url,
                token_server_url="http://127.0.0.1:9",
                token="direct-token",
                api_version="20260101",
            )
            self.assertIs(client.accounts, client.studio.accounts)
            self.assertIs(client.objects, client.store.objects)

            client.accounts.get_current_account()
            client.objects.search_objects(ComplexSearchPayload(limit=1))

        paths = [request["path"] for request in APIHandler.requests]
        self.assertIn("/api/v1/account", paths)
        self.assertIn("/api/v1/objects/search", paths)
        for request in APIHandler.requests:
            self.assertEqual(request["authorization"], "Bearer direct-token")
            self.assertEqual(request["api_version"], "20260101")

    def test_concurrent_secret_key_refresh_is_coalesced(self):
        with TokenServer() as url:
            TokenHandler.responses.append(
                {"token": jwt_with_exp(time.time() + 3600), "token_type": "Bearer", "expires_in": 3600}
            )
            client = Client(api_key="sk-secret", token_server_url=url)
            errors = []

            def read_token():
                try:
                    client.accounts.api_client.configuration.auth_settings()
                except Exception as exc:
                    errors.append(exc)

            threads = [threading.Thread(target=read_token) for _ in range(10)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=5)

        self.assertEqual(errors, [])
        self.assertEqual(len(TokenHandler.requests), 1)

    def test_options_dataclass(self):
        client = Client(ClientOptions(region="us1", token="token"))
        self.assertEqual(client.studio_url, "https://api.us1.vertesia.io/api/v1")


if __name__ == "__main__":
    unittest.main()
