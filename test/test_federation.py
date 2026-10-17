import json
import threading
import unittest
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from vertesia_client import Client, ClientOptions, FederationOptions, VertesiaClientError, exchange_federation_token
from vertesia_client.federation import _FederationTokenSource


class FederationTest(unittest.TestCase):
    def setUp(self):
        self.forms = []
        self.providers = 0
        self.status = 200
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
                data = json.dumps(owner.response).encode()
                self.send_response(owner.status)
                if owner.status == 307:
                    self.send_header("Location", owner.base + "/attacker")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                owner.gets.append(self.headers.get("Authorization"))
                self.send_response(200)
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
        result = exchange_federation_token("idp+token", f, self.options)
        self.assertEqual(result.access_token, "delegated")
        self.assertEqual(result.expires_in, 30)
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
            exchange_federation_token("idp", f, self.options)
        self.assertEqual([item["client_assertion"] for item in self.forms[1:]], [["1"], ["2"]])

    def test_malformed_responses_fail_closed(self):
        for response in [[], {}, {**self.response, "access_token": ""}, {**self.response, "token_type": "Other"}]:
            with self.subTest(response=response):
                self.response = response
                with self.assertRaises(VertesiaClientError):
                    exchange_federation_token("idp", self.federation, self.options)
        for ttl in [0, -1, 301, "30", 1.5, True, None]:
            self.response = {
                "access_token": "token",
                "token_type": "Bearer",
                "issued_token_type": "urn:ietf:params:oauth:token-type:access_token",
                "expires_in": ttl,
            }
            with self.subTest(ttl=ttl), self.assertRaises(VertesiaClientError):
                exchange_federation_token("idp", self.federation, self.options)

    def test_redirects_and_invalid_configuration(self):
        self.status = 307
        with self.assertRaises(VertesiaClientError):
            exchange_federation_token("idp", self.federation, self.options)
        self.assertEqual(len(self.forms), 1)
        for f in [
            replace(self.federation, token_endpoint="http://remote.example/token"),
            replace(self.federation, client_id="m"),
            replace(self.federation, client_id="m", client_secret="secret", client_assertion_provider=lambda: "proof"),
        ]:
            with self.subTest(f=f), self.assertRaises(VertesiaClientError):
                exchange_federation_token("idp", f, self.options)
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
        exchange_federation_token("idp", f, replace(self.options, token="must-not-forward"))
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
        with self.assertRaisesRegex(VertesiaClientError, "expired during exchange"):
            source.token()
