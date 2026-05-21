from __future__ import annotations

import base64
import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

from vertesia_client.openapi.api.access_control_entries_api import AccessControlEntriesApi
from vertesia_client.openapi.api.accounts_api import AccountsApi
from vertesia_client.openapi.api.agent_runs_api import AgentRunsApi
from vertesia_client.openapi.api.api_keys_api import APIKeysApi
from vertesia_client.openapi.api.apps_api import AppsApi
from vertesia_client.openapi.api.audit_trail_api import AuditTrailApi
from vertesia_client.openapi.api.bulk_operations_api import BulkOperationsApi
from vertesia_client.openapi.api.collections_api import CollectionsApi
from vertesia_client.openapi.api.commands_api import CommandsApi
from vertesia_client.openapi.api.content_object_types_api import ContentObjectTypesApi
from vertesia_client.openapi.api.costs_api import CostsApi
from vertesia_client.openapi.api.data_api import DataApi
from vertesia_client.openapi.api.environments_api import EnvironmentsApi
from vertesia_client.openapi.api.files_api import FilesApi
from vertesia_client.openapi.api.interaction_runs_api import InteractionRunsApi
from vertesia_client.openapi.api.interactions_api import InteractionsApi
from vertesia_client.openapi.api.o_auth_clients_api import OAuthClientsApi
from vertesia_client.openapi.api.o_auth_grants_api import OAuthGrantsApi
from vertesia_client.openapi.api.o_auth_providers_api import OAuthProvidersApi
from vertesia_client.openapi.api.objects_api import ObjectsApi
from vertesia_client.openapi.api.processes_api import ProcessesApi
from vertesia_client.openapi.api.projects_api import ProjectsApi
from vertesia_client.openapi.api.prompt_templates_api import PromptTemplatesApi
from vertesia_client.openapi.api.remote_mcp_connections_api import RemoteMCPConnectionsApi
from vertesia_client.openapi.api.rendering_api import RenderingApi
from vertesia_client.openapi.api.roles_api import RolesApi
from vertesia_client.openapi.api.secrets_api import SecretsApi
from vertesia_client.openapi.api.tasks_api import TasksApi
from vertesia_client.openapi.api.token_service_api import TokenServiceApi
from vertesia_client.openapi.api.user_groups_api import UserGroupsApi
from vertesia_client.openapi.api.users_api import UsersApi
from vertesia_client.openapi.api.workflow_definitions_api import WorkflowDefinitionsApi
from vertesia_client.openapi.api.workflow_rules_api import WorkflowRulesApi
from vertesia_client.openapi.api.workflow_runs_api import WorkflowRunsApi
from vertesia_client.openapi.api_client import ApiClient
from vertesia_client.openapi.configuration import Configuration

DEFAULT_SITE = "api.vertesia.io"
DEFAULT_TOKEN_URL = "https://sts.vertesia.io"
DEFAULT_API_VERSION = "20260319"
TOKEN_REFRESH_WINDOW_SECONDS = 60


class VertesiaClientError(ValueError):
    """Raised when the client facade cannot be configured or authenticated."""


@dataclass(frozen=True)
class ClientOptions:
    """Options for the high-level Vertesia client."""

    region: str | None = None
    preview: bool = False
    site: str | None = None
    server_url: str | None = None
    store_url: str | None = None
    token_server_url: str | None = None
    api_key: str | None = None
    token: str | None = None
    api_version: str | None = None


@dataclass(frozen=True)
class _ResolvedEndpoints:
    studio_url: str
    store_url: str
    token_server_url: str
    token_server_url_explicit: bool
    token_server_url_safely_derived: bool


class _TokenSource:
    def token(self) -> str:
        raise NotImplementedError


class _StaticTokenSource(_TokenSource):
    def __init__(self, token: str) -> None:
        self._token = token

    def token(self) -> str:
        return self._token


class _APIKeyTokenSource(_TokenSource):
    def __init__(self, api_key: str, token_server_url: str, api_version: str) -> None:
        self._api_key = api_key
        self._token_server_url = token_server_url
        self._api_version = api_version
        self._token = ""
        self._expires_at = 0.0
        self._lock = threading.Lock()

    def token(self) -> str:
        now = time.time()
        if self._token and now < self._expires_at - TOKEN_REFRESH_WINDOW_SECONDS:
            return self._token

        with self._lock:
            now = time.time()
            if self._token and now < self._expires_at - TOKEN_REFRESH_WINDOW_SECONDS:
                return self._token

            issued = self._issue_token()
            token = str(issued.get("token") or "").strip()
            if not token:
                raise VertesiaClientError("Vertesia STS returned an empty token")

            self._token = token
            self._expires_at = _token_expiry(token, now, issued.get("expires_in"))
            return self._token

    def _issue_token(self) -> dict[str, Any]:
        url = _join_url_path(self._token_server_url, "/token/issue")
        payload = json.dumps({"type": "apikey", "key": self._api_key}).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=payload,
            method="POST",
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
                "x-api-version": self._api_version,
            },
        )

        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                body = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise VertesiaClientError(f"Vertesia STS token exchange failed: HTTP {exc.code}: {body}") from exc
        except urllib.error.URLError as exc:
            raise VertesiaClientError(f"Vertesia STS token exchange failed: {exc.reason}") from exc

        try:
            data = json.loads(body or "{}")
        except json.JSONDecodeError as exc:
            raise VertesiaClientError("Vertesia STS returned invalid JSON") from exc
        if not isinstance(data, dict):
            raise VertesiaClientError("Vertesia STS returned an invalid token payload")
        return data


class _TokenSourceConfiguration(Configuration):
    def __init__(self, host: str, token_source: _TokenSource) -> None:
        super().__init__(host=host)
        self._token_source = token_source

    def auth_settings(self) -> dict[str, dict[str, str]]:
        token = self._token_source.token()
        if not token:
            return {}
        return {
            "bearerAuth": {
                "type": "bearer",
                "in": "header",
                "key": "Authorization",
                "value": f"Bearer {token}",
            }
        }


class GeneratedAPIGroup:
    """Convenience container for generated API classes bound to one base URL."""

    def __init__(self, base_url: str, token_source: _TokenSource, api_version: str) -> None:
        self.api_client = _new_api_client(base_url, token_source, api_version)

        self.access_control_entries = AccessControlEntriesApi(self.api_client)
        self.accounts = AccountsApi(self.api_client)
        self.agent_runs = AgentRunsApi(self.api_client)
        self.api_keys = APIKeysApi(self.api_client)
        self.apps = AppsApi(self.api_client)
        self.audit_trail = AuditTrailApi(self.api_client)
        self.bulk_operations = BulkOperationsApi(self.api_client)
        self.collections = CollectionsApi(self.api_client)
        self.commands = CommandsApi(self.api_client)
        self.content_object_types = ContentObjectTypesApi(self.api_client)
        self.costs = CostsApi(self.api_client)
        self.data = DataApi(self.api_client)
        self.environments = EnvironmentsApi(self.api_client)
        self.files = FilesApi(self.api_client)
        self.interaction_runs = InteractionRunsApi(self.api_client)
        self.interactions = InteractionsApi(self.api_client)
        self.oauth_clients = OAuthClientsApi(self.api_client)
        self.oauth_grants = OAuthGrantsApi(self.api_client)
        self.oauth_providers = OAuthProvidersApi(self.api_client)
        self.objects = ObjectsApi(self.api_client)
        self.processes = ProcessesApi(self.api_client)
        self.projects = ProjectsApi(self.api_client)
        self.prompt_templates = PromptTemplatesApi(self.api_client)
        self.remote_mcp_connections = RemoteMCPConnectionsApi(self.api_client)
        self.rendering = RenderingApi(self.api_client)
        self.roles = RolesApi(self.api_client)
        self.secrets = SecretsApi(self.api_client)
        self.tasks = TasksApi(self.api_client)
        self.token_service = TokenServiceApi(self.api_client)
        self.user_groups = UserGroupsApi(self.api_client)
        self.users = UsersApi(self.api_client)
        self.workflow_definitions = WorkflowDefinitionsApi(self.api_client)
        self.workflow_rules = WorkflowRulesApi(self.api_client)
        self.workflow_runs = WorkflowRunsApi(self.api_client)


class Client:
    """High-level Vertesia client facade over the generated OpenAPI client."""

    def __init__(self, options: ClientOptions | None = None, **kwargs: Any) -> None:
        if options is not None and kwargs:
            raise VertesiaClientError("pass either ClientOptions or keyword options, not both")
        if options is None:
            options = ClientOptions(**kwargs)

        endpoints = _resolve_client_endpoints(options)
        api_version = (options.api_version or DEFAULT_API_VERSION).strip()
        token_source = _new_token_source(options, endpoints, api_version)

        self.studio_url = endpoints.studio_url
        self.store_url = endpoints.store_url
        self.token_server_url = endpoints.token_server_url

        self.studio = GeneratedAPIGroup(endpoints.studio_url, token_source, api_version)
        self.store = GeneratedAPIGroup(endpoints.store_url, token_source, api_version)
        self.token_service = TokenServiceApi(_new_api_client(endpoints.token_server_url, token_source, api_version))

        self.access_control_entries = self.studio.access_control_entries
        self.accounts = self.studio.accounts
        self.api_keys = self.studio.api_keys
        self.apps = self.studio.apps
        self.audit_trail = self.studio.audit_trail
        self.environments = self.studio.environments
        self.interaction_runs = self.studio.interaction_runs
        self.interactions = self.studio.interactions
        self.oauth_clients = self.studio.oauth_clients
        self.oauth_grants = self.studio.oauth_grants
        self.oauth_providers = self.studio.oauth_providers
        self.projects = self.studio.projects
        self.prompt_templates = self.studio.prompt_templates
        self.remote_mcp_connections = self.studio.remote_mcp_connections
        self.roles = self.studio.roles
        self.secrets = self.studio.secrets
        self.user_groups = self.studio.user_groups
        self.users = self.studio.users

        self.agent_runs = self.store.agent_runs
        self.bulk_operations = self.store.bulk_operations
        self.collections = self.store.collections
        self.commands = self.store.commands
        self.content_object_types = self.store.content_object_types
        self.costs = self.store.costs
        self.data = self.store.data
        self.files = self.store.files
        self.objects = self.store.objects
        self.processes = self.store.processes
        self.rendering = self.store.rendering
        self.tasks = self.store.tasks
        self.workflow_definitions = self.store.workflow_definitions
        self.workflow_rules = self.store.workflow_rules
        self.workflow_runs = self.store.workflow_runs


def _new_api_client(base_url: str, token_source: _TokenSource, api_version: str) -> ApiClient:
    api_client = ApiClient(_TokenSourceConfiguration(base_url, token_source))
    api_client.set_default_header("x-api-version", api_version)
    return api_client


def _new_token_source(options: ClientOptions, endpoints: _ResolvedEndpoints, api_version: str) -> _TokenSource:
    api_key = (options.api_key or "").strip()
    token = (options.token or "").strip()

    if api_key and token:
        raise VertesiaClientError("set either api_key or token, not both")
    if token:
        return _StaticTokenSource(token)
    if api_key:
        if not api_key.startswith("sk-"):
            raise VertesiaClientError("api_key must be an sk- secret key")
        if not endpoints.token_server_url_explicit and not endpoints.token_server_url_safely_derived:
            raise VertesiaClientError("token_server_url is required when using api_key with custom endpoints")
        return _APIKeyTokenSource(api_key, endpoints.token_server_url, api_version)
    return _StaticTokenSource("")


def _resolve_client_endpoints(options: ClientOptions) -> _ResolvedEndpoints:
    site = (options.site or "").strip()
    region = (options.region or "").strip()
    server_url = (options.server_url or "").strip()
    store_url = (options.store_url or "").strip()

    if site and region:
        raise VertesiaClientError("set either site or region, not both")
    if region:
        site = _site_from_region(region, options.preview)
    elif not site and options.preview:
        site = _preview_site(DEFAULT_SITE)
    elif not site and not server_url and not store_url:
        site = DEFAULT_SITE

    if not server_url:
        if not site:
            raise VertesiaClientError("site or server_url is required")
        server_url = _site_to_https_url(site)
    if not store_url:
        if not site:
            raise VertesiaClientError("site or store_url is required")
        store_url = _site_to_https_url(site)

    studio_url = _normalize_api_url(server_url)
    normalized_store_url = _normalize_api_url(store_url)

    token_url = (options.token_server_url or "").strip()
    token_url_explicit = bool(token_url)
    token_url_safely_derived = False
    if not token_url:
        token_url, token_url_safely_derived = _derive_token_server_url(site, server_url, store_url)
    token_url = _normalize_server_url(token_url)

    return _ResolvedEndpoints(
        studio_url=studio_url,
        store_url=normalized_store_url,
        token_server_url=token_url,
        token_server_url_explicit=token_url_explicit,
        token_server_url_safely_derived=token_url_safely_derived,
    )


def _site_to_https_url(site: str) -> str:
    if "://" in site:
        return site
    if "/" in site:
        raise VertesiaClientError("site must be a host, not a URL path")
    return f"https://{site}"


def _site_from_region(region: str, preview: bool) -> str:
    value = region.strip().lower()
    if not value or value.startswith("-") or value.endswith("-"):
        raise VertesiaClientError("region must be a region id such as us1 or eu1")
    for char in value:
        if not (char.islower() or char.isdigit() or char == "-"):
            raise VertesiaClientError("region must be a region id such as us1 or eu1")
    site = f"api.{value}.vertesia.io"
    return _preview_site(site) if preview else site


def _preview_site(site: str) -> str:
    if site.startswith("api-preview."):
        return site
    if site.startswith("api."):
        return "api-preview." + site.removeprefix("api.")
    return site


def _normalize_api_url(raw: str) -> str:
    parsed = urllib.parse.urlparse(_normalize_server_url(raw))
    path = parsed.path.rstrip("/")
    if not path:
        path = "/api/v1"
    elif not path.endswith("/api/v1"):
        path = f"{path}/api/v1"
    return urllib.parse.urlunparse(parsed._replace(path=path))


def _normalize_server_url(raw: str) -> str:
    value = raw.strip().rstrip("/")
    parsed = urllib.parse.urlparse(value)
    if not parsed.scheme or not parsed.netloc:
        raise VertesiaClientError("URL must include scheme and host")
    return urllib.parse.urlunparse(parsed._replace(path=parsed.path.rstrip("/")))


def _derive_token_server_url(site: str, server_url: str, store_url: str) -> tuple[str, bool]:
    candidate = site.strip()
    if candidate:
        if "://" not in candidate:
            candidate = f"https://{candidate}"
        token_url = _token_url_from_api_host(candidate)
        if token_url:
            return token_url, True

    token_url = _token_url_from_api_host(server_url)
    if token_url:
        return token_url, True
    token_url = _token_url_from_api_host(store_url)
    if token_url:
        return token_url, True
    return DEFAULT_TOKEN_URL, False


def _token_url_from_api_host(raw: str) -> str:
    parsed = urllib.parse.urlparse(raw.strip())
    hostname = parsed.hostname or ""
    if not hostname.startswith("api"):
        return ""
    sts_host = hostname.replace("api-preview.", "api.", 1)
    sts_host = sts_host.replace("api", "sts", 1)
    return f"https://{sts_host}"


def _join_url_path(base_url: str, path: str) -> str:
    parsed = urllib.parse.urlparse(base_url)
    base_path = parsed.path.rstrip("/")
    suffix = path if path.startswith("/") else f"/{path}"
    return urllib.parse.urlunparse(parsed._replace(path=f"{base_path}{suffix}"))


def _token_expiry(token: str, now: float, expires_in: Any) -> float:
    jwt_exp = _jwt_expiry(token)
    if jwt_exp is not None:
        return jwt_exp
    try:
        ttl = float(expires_in)
    except (TypeError, ValueError):
        ttl = 3600.0
    if ttl <= 0:
        ttl = 3600.0
    return now + ttl


def _jwt_expiry(token: str) -> float | None:
    parts = token.split(".")
    if len(parts) < 2:
        return None
    payload = parts[1]
    payload += "=" * (-len(payload) % 4)
    try:
        claims = json.loads(base64.urlsafe_b64decode(payload.encode("ascii")))
    except (ValueError, json.JSONDecodeError):
        return None
    exp = claims.get("exp") if isinstance(claims, dict) else None
    try:
        return float(exp)
    except (TypeError, ValueError):
        return None
