"""Server-to-server federation without modifying generated OpenAPI code."""

from __future__ import annotations

import base64
import json
import math
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from http.client import HTTPException
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from vertesia_client.client import ClientOptions


@dataclass(frozen=True)
class FederationOptions:
    """Use one configuration per user/project/resource/scope; providers return fresh assertions."""

    connection_id: str
    project_id: str
    resource: str
    subject_token_provider: Callable[[], str] | None = field(default=None, repr=False)
    scope: str | None = None
    subject_token_type: str = "urn:ietf:params:oauth:token-type:jwt"
    token_endpoint: str | None = None
    client_secret_basic: bool = False
    client_id: str | None = None
    client_secret: str | None = field(default=None, repr=False)
    client_assertion_provider: Callable[[], str] | None = field(default=None, repr=False)


@dataclass(frozen=True)
class FederationToken:
    """A one-shot token with conservative remaining expires_in seconds; no refresh token."""

    access_token: str = field(repr=False)
    expires_in: int
    token_type: str = "Bearer"
    scope: str | None = None


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _endpoint(federation: FederationOptions, options: ClientOptions) -> str:
    from vertesia_client.client import VertesiaClientError, _resolve_client_endpoints

    f = federation
    if not f.connection_id or not f.project_id or not f.resource:
        raise VertesiaClientError("federation requires connection_id, project_id and resource")
    if (
        (f.client_secret_basic and not f.client_secret)
        or bool(f.client_secret or f.client_assertion_provider) != bool(f.client_id)
        or (f.client_secret and f.client_assertion_provider)
    ):
        raise VertesiaClientError("client authentication requires client_id and exactly one credential method")
    endpoint = f.token_endpoint
    if not endpoint:
        endpoints = _resolve_client_endpoints(options)
        host = urllib.parse.urlsplit(endpoints.token_server_url).hostname or ""
        hosted = host == "sts.vertesia.io" or (host.startswith("sts.") and host.endswith(".vertesia.io"))
        if not endpoints.token_server_url_explicit and not (endpoints.token_server_url_safely_derived and hosted):
            raise VertesiaClientError("explicit federation token_server_url or token_endpoint required for custom URLs")
        endpoint = endpoints.token_server_url + "/oauth/token"
    url = urllib.parse.urlsplit(endpoint)
    if (
        not url.hostname
        or url.username is not None
        or url.password is not None
        or url.query
        or url.fragment
        or not (url.scheme == "https" or (url.scheme == "http" and url.hostname in {"localhost", "127.0.0.1", "::1"}))
    ):
        raise VertesiaClientError(
            "federation endpoint must use HTTPS (or loopback HTTP), without credentials/query/fragment"
        )
    return endpoint


def exchange_federation_token(
    options: ClientOptions | None, federation: FederationOptions, subject_token: str
) -> FederationToken:
    """Exchange one assertion without caching or changing any client authentication."""
    return _exchange_federation_token(subject_token, federation, options, time.monotonic)


def _exchange_federation_token(subject_token, federation, options, clock):
    from vertesia_client.client import ClientOptions, FederationExchangeError, VertesiaClientError

    options = options or ClientOptions()
    endpoint = _endpoint(federation, options)
    if not isinstance(subject_token, str) or not subject_token.strip():
        raise VertesiaClientError("empty IdP assertion")
    values = {
        "grant_type": "urn:ietf:params:oauth:grant-type:token-exchange",
        "subject_token": subject_token,
        "subject_token_type": federation.subject_token_type,
        "federation_connection_id": federation.connection_id,
        "project_id": federation.project_id,
        "resource": federation.resource,
    }
    if federation.scope:
        values["scope"] = federation.scope
    if federation.client_id and not federation.client_secret_basic:
        values["client_id"] = federation.client_id
    if federation.client_secret and not federation.client_secret_basic:
        values["client_secret"] = federation.client_secret
    if federation.client_assertion_provider:
        try:
            assertion = federation.client_assertion_provider()
        except Exception:
            raise VertesiaClientError("client assertion provider failed") from None
        if not isinstance(assertion, str) or not assertion.strip():
            raise VertesiaClientError("empty client assertion")
        values["client_assertion"] = assertion
        values["client_assertion_type"] = "urn:ietf:params:oauth:client-assertion-type:jwt-bearer"
    request = urllib.request.Request(
        endpoint,
        data=urllib.parse.urlencode(values).encode(),
        headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
        method="POST",
    )
    if federation.client_secret_basic:
        credentials = (
            urllib.parse.quote_plus(federation.client_id) + ":" + urllib.parse.quote_plus(federation.client_secret)
        )
        request.add_header("Authorization", "Basic " + base64.b64encode(credentials.encode()).decode())
    started = clock()
    try:
        with urllib.request.build_opener(_NoRedirect()).open(request, timeout=60) as response:
            body = response.read(65537)
            if response.status != 200 or len(body) > 65536:
                raise VertesiaClientError("invalid federation response")
    except urllib.error.HTTPError as exc:
        code = exc.code
        try:
            body = exc.read(65537)
            data = json.loads(body) if len(body) <= 65536 else {}
            oauth_code = data.get("error") if isinstance(data, dict) else None
        except (ValueError, OSError, HTTPException):
            oauth_code = None
        finally:
            exc.close()
        if oauth_code not in (
            "invalid_grant",
            "invalid_client",
            "invalid_scope",
            "unauthorized_client",
            "invalid_target",
            "invalid_request",
            "temporarily_unavailable",
        ):
            oauth_code = None
        raise FederationExchangeError(code, oauth_code) from None
    except (OSError, urllib.error.URLError, HTTPException):
        raise VertesiaClientError("federation exchange request failed") from None
    try:
        data = json.loads(body)
        token = data["access_token"]
        ttl = data["expires_in"]
        if (
            not isinstance(token, str)
            or not re.fullmatch(r"[A-Za-z0-9._~+/-]+=*", token)
            or type(ttl) not in (int, float)
            or not math.isfinite(ttl)
            or not 1 <= ttl <= 86400
            or not isinstance(data["token_type"], str)
            or data["token_type"].lower() != "bearer"
            or data["issued_token_type"] != "urn:ietf:params:oauth:token-type:access_token"
        ):
            raise ValueError
        remaining = math.floor(ttl - max(0.0, clock() - started))
        if remaining < 1:
            raise VertesiaClientError("federation token expired during exchange")
        return FederationToken(token, remaining, scope=data.get("scope"))
    except VertesiaClientError:
        raise
    except (ValueError, TypeError, KeyError):
        raise VertesiaClientError("invalid federation token response") from None


class _FederationTokenSource:
    def __init__(self, federation: FederationOptions, options: ClientOptions) -> None:
        from vertesia_client.client import VertesiaClientError

        _endpoint(federation, options)
        if not callable(federation.subject_token_provider):
            raise VertesiaClientError("federation requires subject_token_provider")
        self.federation = federation
        self.options = options
        self._clock = time.monotonic
        self._lock = threading.Lock()
        self._token = ""
        self._renew_at = 0.0
        self._failure = None
        self._retry_at = 0.0

    def token(self) -> str:
        from vertesia_client.client import VertesiaClientError

        with self._lock:
            now = self._clock()
            if self._token and now < self._renew_at:
                return self._token
            if self._failure is not None and now < self._retry_at:
                raise self._failure.with_traceback(None) from None
            try:
                issued = self._renew()
            except VertesiaClientError as error:
                self._failure = error
                self._retry_at = self._clock() + 1.0
                raise
            self._failure = None
            self._token = issued.access_token
            self._renew_at = self._clock() + issued.expires_in * 0.9
            return self._token

    def invalidate(self, token):
        with self._lock:
            if self._token == token:
                self._token = ""
                self._failure = None

    def _renew(self):
        from vertesia_client.client import VertesiaClientError

        try:
            assertion = self.federation.subject_token_provider()
        except Exception:
            raise VertesiaClientError("IdP token provider failed") from None
        return _exchange_federation_token(assertion, self.federation, self.options, self._clock)
