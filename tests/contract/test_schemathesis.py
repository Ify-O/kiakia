"""Property-based contract testing with Schemathesis.

Schemathesis reads openapi.yaml (served by the app at /openapi.json, see api/app.py) and
generates many requests, valid and invalid, for every operation. For each response it checks:
no 5xx, status code / content type / headers / body all match the contract.

The reply endpoint (POST) is excluded until it's implemented (next step of Assignment 2).
"""

import pytest
import schemathesis
from hypothesis import HealthCheck, settings
from schemathesis.checks import positive_data_acceptance

pytestmark = pytest.mark.integration


@pytest.fixture
def api_schema(client):
    return schemathesis.openapi.from_asgi("/openapi.json", client.app).exclude(operation_id="createReply")


schema = schemathesis.pytest.from_fixture("api_schema")


@schema.parametrize()
@settings(max_examples=30, deadline=None, suppress_health_check=list(HealthCheck))
def test_api_matches_contract(case):
    # A cursor is an opaque token: only values the server issued are valid, which no schema can
    # express. So for requests with a made-up cursor we skip ONLY the "valid-looking input must be
    # accepted" check (they correctly get 400). Every other check still runs on them.
    excluded = [positive_data_acceptance] if "cursor" in (case.query or {}) else []
    case.call_and_validate(excluded_checks=excluded)
