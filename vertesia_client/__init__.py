"""Vertesia Python client."""

from vertesia_client.client import Client, ClientOptions, FederationExchangeError, VertesiaClientError

__all__ = [
    "Client",
    "ClientOptions",
    "VertesiaClientError",
    "FederationExchangeError",
    "FederationOptions",
    "FederationToken",
    "exchange_federation_token",
]

from vertesia_client.federation import FederationOptions, FederationToken, exchange_federation_token
