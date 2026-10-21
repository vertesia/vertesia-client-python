# Vertesia Python Client

Python client for the Vertesia API.

```sh
pip install vertesia-client
```

## Quick Start

```python
from vertesia_client import Client

client = Client(api_key="sk-...")
account = client.accounts.get_current_account()
print(account.name)
```

`Client` is the recommended entry point. It configures the generated clients,
routes Studio and Store APIs, exchanges secret keys for short-lived tokens, and
sets the stable `x-api-version` header.

## Authentication

Use an `sk-` secret key when you want the client to exchange credentials through
STS:

```python
client = Client(api_key="sk-...")
```

Use `token` when you already have a bearer token:

```python
client = Client(token="eyJ...")
```

Set either `api_key` or `token`, not both.

## Endpoints

By default the client uses the unified global API:

```python
client = Client(api_key="sk-...")
```

Use `region` for a hosted regional API:

```python
client = Client(region="us1", api_key="sk-...")
```

Use `preview` when you need the hosted preview API:

```python
client = Client(region="us1", preview=True, api_key="sk-...")
```

For public client usage, prefer the default global API or a hosted Vertesia region.
Studio and Store requests are routed through the same public API host.

`site` is available as an advanced override when you need to provide the exact
Vertesia API host:

```python
client = Client(site="api.us1.vertesia.io", api_key="sk-...")
```

`server_url`, `store_url`, and `token_server_url` are intended for Vertesia
developers testing split local or branch environments. When using `api_key` with
custom split endpoints, set `token_server_url` unless STS can be inferred from a
Vertesia `api*` host. Direct `token` authentication does not require STS.

The facade exposes both generated clients:

```python
client.studio.projects
client.store.objects
```

It also exposes convenience aliases such as `client.accounts` and
`client.objects` where routing is unambiguous.

## Raw Generated Client

The OpenAPI generated client remains available for advanced use:

```python
from vertesia_client.openapi.api.accounts_api import AccountsApi
from vertesia_client.openapi.api_client import ApiClient
from vertesia_client.openapi.configuration import Configuration

configuration = Configuration(host="https://api.us1.vertesia.io/api/v1", access_token="YOUR_TOKEN")
api_client = ApiClient(configuration)
api_client.set_default_header("x-api-version", "20260319")

account = AccountsApi(api_client).get_current_account()
```

Generated APIs and models are importable from `vertesia_client.openapi`.

## Testing

Unit tests run without credentials:

```sh
python -m unittest discover -s test
```

Live integration tests are opt-in. They run only when `VERTESIA_LIVE_TESTS=1`
and `VERTESIA_API_KEY` is set to a non-placeholder `sk-` secret key. For
Vertesia developers running the client tests locally:

```sh
cp .env.example .env
# Edit .env and set VERTESIA_LIVE_TESTS=1 plus VERTESIA_API_KEY=sk-...
python -m unittest discover -s test
```

Without `VERTESIA_LIVE_TESTS=1`, live integration tests are skipped even when a
local `.env` file exists.

The `.env` file is for local development only and should not be committed.

## Generation

This repository is generated from the Vertesia OpenAPI specification. Generated
code is committed so Python consumers can install released packages without
running OpenAPI Generator.

The public OpenAPI contract for the committed client is tracked at
`spec/vertesia-openapi.json` and `spec/vertesia-openapi.yaml`. The JSON file is
the source used to generate the committed client source; the YAML file is
included for tools and readers that prefer YAML.

To regenerate locally, refresh the tracked spec files, then run:

```sh
openapi-generator generate -c openapi-generator-config.yaml
python -m unittest discover -s test
```

Generated files under `vertesia_client/openapi/` and `spec/` are owned by
internal generation automation. Do not edit or commit them manually. Pull
requests that change either directory are rejected by CI.

Hand-written client surface changes, such as `Client`, tests, examples, README,
workflows, and generator configuration, should go through normal pull request
review.

The generation automation should regenerate the client, run tests, and push the
generated diff directly to `main` using a dedicated bot or GitHub App. It should
stage only the expected generated files and package metadata:

```sh
git add vertesia_client/openapi spec pyproject.toml .openapi-generator
```

## Compatibility

The generated Python models ignore unknown response fields, so newer server
fields do not break older clients. Unknown enum values are accepted for forward
compatibility as well: standalone generated enum classes preserve the raw
unknown value, and inline enum validators do not reject values that already
match the underlying JSON type.

### Generated API version headers

The high-level client supplies required generated `x_api_version` arguments from its configured API version before validation, including calls with positional payloads. A per-call `x_api_version` keyword overrides that default. Raw generated clients still require the declared arguments.

## OIDC federation for provisioned users

Use one client per user/project/resource/scope combination. Users must already be SCIM-provisioned
and the organization must configure federation trust. The facade does not create or adopt users.

```python
from vertesia_client import Client, ClientOptions, FederationOptions, exchange_federation_token

policy = FederationOptions(
    connection_id="connection-id",
    project_id="project-id",
    resource="https://api.us1.vertesia.io",
    subject_token_provider=get_fresh_idp_token,  # callable returning a current assertion
    # scope="content:read",  # omit for current delegable permissions
)
options = ClientOptions(region="us1", federation=policy)
client = Client(options)
# Use client.projects, client.objects, etc. as usual.

# One-shot exchange; caller manages token lifetime:
issued = exchange_federation_token(options, policy, idp_assertion)
# issued.access_token, issued.expires_in, issued.scope
```

The first authenticated request exchanges an assertion. Studio and Store share the cached token;
after 90% of its lifetime, the next request obtains a fresh assertion and exchanges again. Renewal
is serialized across threads, uses no background worker or refresh token, and propagates failures
instead of sending expired credentials. API writes are not automatically retried on a 401. Your
provider must fetch a current assertion on each renewal. `federation`, `api_key` and `token` are
mutually exclusive.

`subject_token_type` defaults to `urn:ietf:params:oauth:token-type:jwt`. The endpoint defaults to
resolved STS plus `/oauth/token`; use `token_endpoint` for a full tenant endpoint or explicit
`token_server_url` for custom split URLs. HTTPS is required except loopback HTTP; redirects are
rejected. Assertions and secrets are excluded from the new configuration/result representations
and exchange error messages.

Optional middleware authentication: set `client_id` plus `client_secret` for `client_secret_post`;
add `client_secret_basic=True` for Basic instead. Or set `client_id` and a
`client_assertion_provider` callback for `private_key_jwt`. It must mint a new short-lived signed
assertion with a unique `jti` for each exchange; the single-string `aud` must be the Vertesia
authorization-server issuer from discovery, including any tenant. Do not mix authentication methods.

Exchange results report conservative remaining `expires_in` seconds after subtracting the STS
round trip (rounded up). Time spent obtaining IdP/client assertions does not consume the delegated
token's lifetime. Already-expired results are rejected in both one-shot and automatic modes.
Implicit STS routing is limited to recognized Vertesia STS domains; custom `api*` hostnames require
an explicit STS URL or complete token endpoint, even when their names resemble hosted endpoints.

An API 401 invalidates only the bearer token that request used. The SDK does not replay the request;
the next call attempts a fresh exchange. Concurrent late 401s do not evict a different newer token.
Failed automatic exchanges are cached for one second to avoid repeatedly calling the IdP/STS during
an outage. Finite lifetimes up to one day are accepted, including fractional seconds; returned
remaining seconds are rounded down. The server's current five-minute cap is not a client limit.

Catch `FederationExchangeError` to inspect `status_code` and `error_code`, for example to distinguish
`invalid_scope` from `invalid_grant` or identify HTTP 429/503. Only known OAuth codes are exposed;
remote descriptions and unknown error text remain redacted. The one-shot function takes arguments
in the same order as the Go/Java facades: options, federation policy, subject assertion.

## Release

The Release workflow accepts a synchronized spec version such as `v1.6.0` from
`release/1.6`, or `v1.6.0-dev.20261021.123456Z` from `main`. Optional `source_sha`
pins the exact tested commit. `dry_run=true` (the default) validates, tests and
builds distributions without creating tags or publishing.

Dev artifact versions use PEP 440, for example `1.6.0.dev20261021123456`. The
publisher stamps this unique version in the build workspace; generator/source
metadata can keep its existing `.dev0` convention. PyPI trusted publishing uses
the existing `pypi` environment and Release workflow identity. GitHub releases
are marked as prereleases for dev cohorts. Retries never replace a source tag;
an already published version requires a matching tag at the same commit.
