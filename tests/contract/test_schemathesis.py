"""Property-based contract testing with Schemathesis.

Schemathesis reads openapi.yaml (served by the app at /openapi.json, see api/app.py) and
generates many requests, valid and invalid, for every operation. For each response it checks:
no 5xx, status code / content type / headers / body all match the contract.

All operations are covered, including POST /replies (sent to a fake channel, never to real people).
"""

import pytest
import schemathesis
from hypothesis import HealthCheck, settings
from schemathesis.checks import positive_data_acceptance

pytestmark = pytest.mark.integration


@pytest.fixture
def api_schema(client):
    return schemathesis.openapi.from_asgi("/openapi.json", client.app)


schema = schemathesis.pytest.from_fixture("api_schema")


@schema.parametrize()
@settings(max_examples=30, deadline=None, suppress_health_check=list(HealthCheck))
def test_api_matches_contract(case):
    # A cursor is an opaque token: only values the server issued are valid, which no schema can
    # express. So for requests with a made-up cursor we skip ONLY the "valid-looking input must be
    # accepted" check (they correctly get 400). Every other check still runs on them.
    # Same for from/to: "from must be before to" is a rule BETWEEN two parameters (docs/adr/0006),
    # which OpenAPI can't express, so a valid-looking pair may correctly get 400.
    query = case.query or {}
    opaque_or_cross_field = "cursor" in query or ("from" in query and "to" in query)
    excluded = [positive_data_acceptance] if opaque_or_cross_field else []
    case.call_and_validate(excluded_checks=excluded)
