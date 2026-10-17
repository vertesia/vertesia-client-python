import json
import threading
import unittest
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import MagicMock, patch

from vertesia_client import Client, ClientOptions, FederationOptions, VertesiaClientError, exchange_federation_token
from vertesia_client.federation import _endpoint, _exchange_federation_token, _FederationTokenSource


class FederationTest(unittest.TestCase):
    def setUp(self):
        self.forms = []
        self.providers = 0
        self.status = 200
        self.api_status = 200
        self.malformed_status = False
        self.response = {
            "access_token": "delegated",
            "token_type": "Bearer",
            "issued_token_type": "urn:ietf:params:oauth:token-type:access_token",
            "expires_in": 30,
        }
        self.gets = []
        self.sts_headers = []
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                owner.sts_headers.append(self.headers.get("Authorization"))
                owner.forms.append(urllib.parse.parse_qs(self.rfile.read(int(self.headers["Content-Length"])).decode()))
                if owner.malformed_status:
                    self.connection.sendall(b"sensitive-idp-status\r\n\r\n")
                    self.close_connection = True
                    return
                data = json.dumps(owner.response).encode()
                self.send_response(owner.status)
                if owner.status == 307:
                    self.send_header("Location", owner.base + "/attacker")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                owner.gets.append(self.headers.get("Authorization"))
                self.send_response(owner.api_status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", "2")
                self.end_headers()
                self.wfile.write(b"[]")

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"
        self.federation = FederationOptions("connection", "project", self.base, self.provider, scope="content:read")
        self.options = ClientOptions(server_url=self.base, store_url=self.base, token_server_url=self.base)

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def provider(self):
        self.providers += 1
        return f"fresh+idp-{self.providers}"

    def test_cache_concurrency_and_renewal_failure(self):
        source = _FederationTokenSource(self.federation, self.options)
        now = [100.0]
        source._clock = lambda: now[0]
        with ThreadPoolExecutor(max_workers=10) as executor:
            self.assertEqual(list(executor.map(lambda _: source.token(), range(20))), ["delegated"] * 20)
        self.assertEqual(len(self.forms), 1)
        self.assertEqual(self.providers, 1)
        now[0] += 28
        self.assertEqual(source.token(), "delegated")
        self.assertEqual(self.forms[-1]["subject_token"], ["fresh+idp-2"])
        now[0] += 31
        self.status = 401
        self.response = {"error": "sensitive-idp-secret"}
        with self.assertRaises(VertesiaClientError) as error:
            source.token()
        self.assertNotIn("sensitive", str(error.exception))

    def test_facade_routes_and_shares_cache(self):
        client = Client(replace(self.options, federation=self.federation))
        client.projects.list_projects()
        client.store.projects.list_projects()
        self.assertEqual(self.gets, ["Bearer delegated", "Bearer delegated"])
        self.assertEqual(len(self.forms), 1)
        self.assertEqual(self.sts_headers, [None])
        self.assertEqual(self.forms[0]["project_id"], ["project"])
        self.assertEqual(self.forms[0]["resource"], [self.base])
        self.assertEqual(self.forms[0]["scope"], ["content:read"])

    def test_one_shot_tenant_endpoint_and_authentication(self):
        f = replace(
            self.federation, token_endpoint=self.base + "/tenant/token", client_id="m", client_secret="a+b%secret"
        )
        result = exchange_federation_token(self.options, f, "idp+token")
        self.assertEqual(result.access_token, "delegated")
        self.assertGreaterEqual(result.expires_in, 28)
        self.assertLessEqual(result.expires_in, 30)
        self.assertEqual(self.forms[0]["client_secret"], ["a+b%secret"])
        self.assertEqual(self.forms[0]["subject_token"], ["idp+token"])
        self.assertEqual(self.providers, 0)
        self.assertNotIn("a+b%secret", repr(f))
        self.assertNotIn("delegated", repr(result))
        count = [0]

        def proof():
            count[0] += 1
            return str(count[0])

        f = replace(f, client_secret=None, client_assertion_provider=proof)
        for _ in range(2):
            exchange_federation_token(self.options, f, "idp")
        self.assertEqual([item["client_assertion"] for item in self.forms[1:]], [["1"], ["2"]])

    def test_malformed_responses_fail_closed(self):
        for response in [[], {}, {**self.response, "access_token": ""}, {**self.response, "token_type": "Other"}]:
            with self.subTest(response=response):
                self.response = response
                with self.assertRaises(VertesiaClientError):
                    exchange_federation_token(self.options, self.federation, "idp")
        for ttl in [0, -1, 86401, "30", True, None]:
            self.response = {
                "access_token": "token",
                "token_type": "Bearer",
                "issued_token_type": "urn:ietf:params:oauth:token-type:access_token",
                "expires_in": ttl,
            }
            with self.subTest(ttl=ttl), self.assertRaises(VertesiaClientError):
                exchange_federation_token(self.options, self.federation, "idp")

    def test_redirects_and_invalid_configuration(self):
        self.status = 307
        with self.assertRaises(VertesiaClientError):
            exchange_federation_token(self.options, self.federation, "idp")
        self.assertEqual(len(self.forms), 1)
        for f in [
            replace(self.federation, token_endpoint="http://remote.example/token"),
            replace(self.federation, client_id="m"),
            replace(self.federation, client_id="m", client_secret="secret", client_assertion_provider=lambda: "proof"),
        ]:
            with self.subTest(f=f), self.assertRaises(VertesiaClientError):
                exchange_federation_token(self.options, f, "idp")
        with self.assertRaises(VertesiaClientError):
            Client(replace(self.options, federation=self.federation, token="direct"))
        with self.assertRaises(VertesiaClientError):
            Client(replace(self.options, federation=replace(self.federation, subject_token_provider=None)))

    def test_provider_failure_is_redacted_and_makes_no_request(self):
        def fail():
            raise RuntimeError("sensitive-provider-secret")

        source = _FederationTokenSource(replace(self.federation, subject_token_provider=fail), self.options)
        with self.assertRaises(VertesiaClientError) as error:
            source.token()
        self.assertNotIn("sensitive-provider-secret", str(error.exception))
        self.assertEqual(self.forms, [])

    def test_basic_encoding_and_exchange_that_outlives_token(self):
        import base64

        f = replace(self.federation, client_id="client+id", client_secret="a+b%:secret", client_secret_basic=True)
        exchange_federation_token(replace(self.options, token="must-not-forward"), f, "idp")
        encoded = self.sts_headers[0].removeprefix("Basic ")
        self.assertEqual(base64.b64decode(encoded).decode(), "client%2Bid:a%2Bb%25%3Asecret")
        self.assertNotIn("client_secret", self.forms[0])
        self.assertNotIn("client_id", self.forms[0])
        now = [0.0]

        def slow_provider():
            now[0] += 60
            return "idp"

        source = _FederationTokenSource(replace(f, subject_token_provider=slow_provider), self.options)
        source._clock = lambda: now[0]
        self.assertEqual(source.token(), "delegated")

    def test_custom_sts_must_be_explicit_in_both_modes(self):
        for host in ["api.example.com", "api.vertesia.io.attacker.test", "api.evilvertesia.io"]:
            options = ClientOptions(server_url="https://" + host, store_url="https://store.example.com")
            with self.subTest(host=host):
                with self.assertRaises(VertesiaClientError):
                    Client(replace(options, federation=self.federation))
                with self.assertRaises(VertesiaClientError):
                    exchange_federation_token(options, self.federation, "idp")
                Client(replace(options, token_server_url=self.base, federation=self.federation))
                Client(
                    replace(options, federation=replace(self.federation, token_endpoint=self.base + "/tenant/token"))
                )
        for options in [
            ClientOptions(),
            ClientOptions(region="us1", preview=True),
            ClientOptions(site="api.us1.harness.vertesia.io"),
        ]:
            self.assertIn("vertesia.io", _endpoint(self.federation, options))
        self.assertEqual(self.forms, [])
        self.assertEqual(self.providers, 0)

    def test_malformed_http_status_is_redacted(self):
        self.malformed_status = True
        with self.assertRaises(VertesiaClientError) as error:
            exchange_federation_token(self.options, self.federation, "idp")
        self.assertNotIn("sensitive-idp-status", str(error.exception))
        self.assertTrue(error.exception.__suppress_context__)

    def test_shared_transit_accounting_and_bearer_validation(self):
        now = [0.0]
        response = MagicMock()
        response.status = 200
        response.__enter__.return_value = response
        delay = [2.0]
        data = {**self.response, "token_type": "bearer"}

        def read(_limit):
            now[0] += delay[0]
            return json.dumps(data).encode()

        response.read.side_effect = read
        opener = MagicMock()
        opener.open.return_value = response

        def slow_proof():
            now[0] += 60
            return "proof"

        f = replace(self.federation, client_id="m", client_assertion_provider=slow_proof)
        with patch("urllib.request.build_opener", return_value=opener):
            result = _exchange_federation_token("idp", f, self.options, lambda: now[0])
            self.assertEqual(result.expires_in, 28)
            delay[0] = 31
            with self.assertRaisesRegex(VertesiaClientError, "expired during exchange"):
                _exchange_federation_token("idp", f, self.options, lambda: now[0])
            delay[0] = 0
            data["access_token"] = "bad\ntoken"
            with self.assertRaises(VertesiaClientError):
                exchange_federation_token(self.options, f, "idp")

    def test_allowlisted_oauth_error_metadata(self):
        from vertesia_client import FederationExchangeError

        self.status = 400
        for code in [
            "invalid_grant",
            "invalid_client",
            "invalid_scope",
            "unauthorized_client",
            "invalid_target",
            "invalid_request",
            "temporarily_unavailable",
        ]:
            self.response = {"error": code, "error_description": "secret-idp-token"}
            with self.assertRaises(FederationExchangeError) as error:
                exchange_federation_token(self.options, self.federation, "idp")
            self.assertEqual(error.exception.status_code, 400)
            self.assertEqual(error.exception.error_code, code)
            self.assertNotIn("secret-idp-token", str(error.exception))
        self.status = 503
        self.response = {"error": "secret-idp-token"}
        with self.assertRaises(FederationExchangeError) as error:
            exchange_federation_token(self.options, self.federation, "idp")
        self.assertEqual(error.exception.status_code, 503)
        self.assertIsNone(error.exception.error_code)
        self.assertNotIn("secret-idp-token", str(error.exception))

    def test_401_invalidates_without_replaying_and_failures_back_off(self):
        from vertesia_client.openapi.exceptions import ApiException

        client = Client(replace(self.options, federation=self.federation))
        self.api_status = 401
        with self.assertRaises(ApiException):
            client.projects.list_projects()
        self.assertEqual(len(self.forms), 1)
        self.assertEqual(len(self.gets), 1)
        self.api_status = 200
        client.store.projects.list_projects()
        self.assertEqual(len(self.forms), 2)
        self.assertEqual(len(self.gets), 2)
        source = _FederationTokenSource(self.federation, self.options)
        now = [0.0]
        source._clock = lambda: now[0]
        self.status = 429
        initial = len(self.forms)
        for _ in range(10):
            with self.assertRaises(VertesiaClientError):
                source.token()
        self.assertEqual(len(self.forms), initial + 1)
        now[0] += 2
        with self.assertRaises(VertesiaClientError):
            source.token()
        self.assertEqual(len(self.forms), initial + 2)
        source._token = "new"
        source.invalidate("old")
        self.assertEqual(source._token, "new")

    def test_accepts_raised_fractional_lifetimes(self):
        self.response["expires_in"] = 600.75
        token = _exchange_federation_token("idp", self.federation, self.options, lambda: 0.0)
        self.assertEqual(token.expires_in, 600)
