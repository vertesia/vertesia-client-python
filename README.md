# Vertesia Python Client

Python SDK for the Vertesia API.

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

Use an `sk-` secret key when you want the SDK to exchange credentials through
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

For public SDK usage, prefer the default global API or a hosted Vertesia region.
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
Vertesia developers running the SDK tests locally:

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

The public OpenAPI contract for the committed SDK is tracked at
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

Hand-written SDK surface changes, such as `Client`, tests, examples, README,
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
fields do not break older SDKs. Unknown enum values are accepted for forward
compatibility as well: standalone generated enum classes preserve the raw
unknown value, and inline enum validators do not reject values that already
match the underlying JSON type.
