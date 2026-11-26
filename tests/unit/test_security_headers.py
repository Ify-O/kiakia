"""The API's security headers (kiakia/api/security.py), tested on a tiny app: no database needed."""

from fastapi import FastAPI, Response
from fastapi.testclient import TestClient

from kiakia.api.security import SecurityHeaders


def make_client() -> TestClient:
    app = FastAPI()
    app.add_middleware(SecurityHeaders)

    @app.get("/v1/customers")
    def customers() -> dict[str, list[str]]:
        return {"items": []}

    @app.get("/v1/exports/customers")
    def export() -> Response:
        return Response("a,b", headers={"Cache-Control": "no-store, private"})

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return TestClient(app)


def test_every_api_response_gets_the_browser_protections():
    r = make_client().get("/v1/customers")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-frame-options"] == "DENY"
    assert r.headers["referrer-policy"] == "no-referrer"
    assert r.headers["cross-origin-resource-policy"] == "same-origin"
    assert r.headers["content-security-policy"] == "default-src 'none'; frame-ancestors 'none'"


def test_personal_data_is_never_cached():
    assert make_client().get("/v1/customers").headers["cache-control"] == "no-store"


def test_a_route_that_sets_its_own_header_keeps_it_and_is_not_duplicated():
    r = make_client().get("/v1/exports/customers")
    assert r.headers.get_list("cache-control") == ["no-store, private"]


def test_health_is_not_personal_data_but_still_protected():
    r = make_client().get("/health")
    assert "cache-control" not in r.headers
    assert r.headers["x-content-type-options"] == "nosniff"


def test_swagger_docs_keep_working_without_the_strict_policy():
    r = make_client().get("/docs")
    assert r.status_code == 200
    assert "content-security-policy" not in r.headers
    assert r.headers["x-frame-options"] == "DENY"
