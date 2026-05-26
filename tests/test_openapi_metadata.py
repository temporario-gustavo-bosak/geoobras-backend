from __future__ import annotations

from fastapi.testclient import TestClient

from src.api.main import app

_EXPECTED_TAG_NAMES = {"Obras", "Insights", "Estatísticas", "Operação"}


def _schema() -> dict:
    with TestClient(app) as client:
        resp = client.get("/openapi.json")
    assert resp.status_code == 200
    return resp.json()


# ---------------------------------------------------------------------------
# E-01: OpenAPI metadata tests
# ---------------------------------------------------------------------------


def test_openapi_info_contact_present() -> None:
    """info.contact must include name, url, and email."""
    info = _schema()["info"]
    contact = info.get("contact", {})
    assert contact.get("name"), "info.contact.name is missing"
    assert contact.get("url"), "info.contact.url is missing"
    assert contact.get("email"), "info.contact.email is missing"


def test_openapi_info_license_present() -> None:
    """info.license must include name and url."""
    info = _schema()["info"]
    license_ = info.get("license", {})
    assert license_.get("name"), "info.license.name is missing"
    assert license_.get("url"), "info.license.url is missing"


def test_openapi_tag_groups_present() -> None:
    """openapi.json must declare all four tag groups."""
    tags = {t["name"] for t in _schema().get("tags", [])}
    assert _EXPECTED_TAG_NAMES <= tags, f"Missing tags: {_EXPECTED_TAG_NAMES - tags}"


def test_all_routes_have_a_declared_tag() -> None:
    """Every path operation must carry at least one of the declared tag names."""
    schema = _schema()
    untagged = []
    for path, methods in schema.get("paths", {}).items():
        for method, operation in methods.items():
            op_tags = set(operation.get("tags", []))
            if not op_tags & _EXPECTED_TAG_NAMES:
                untagged.append(f"{method.upper()} {path}")
    assert not untagged, f"Operations missing a declared tag: {untagged}"


def test_health_still_returns_200() -> None:
    """App boots correctly and /health responds after metadata enrichment."""
    with TestClient(app) as client:
        response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


# ---------------------------------------------------------------------------
# E-02: response examples and schema docs smoke tests
# ---------------------------------------------------------------------------


def test_obras_list_endpoint_has_response_example() -> None:
    """
    Smoke test: GET /api/v1/obras must expose an inline example in the OpenAPI spec
    so Swagger UI renders it without a live DB connection.
    """
    schema = _schema()
    example = (
        schema.get("paths", {})
        .get("/api/v1/obras", {})
        .get("get", {})
        .get("responses", {})
        .get("200", {})
        .get("content", {})
        .get("application/json", {})
        .get("example")
    )
    assert example is not None, "No response example found for GET /api/v1/obras"
    assert "items" in example, "Example must contain an 'items' list"
    assert "total" in example, "Example must contain a 'total' count"


def test_swagger_ui_parameters_present() -> None:
    """FastAPI constructor must declare swagger_ui_parameters for /docs customisation."""
    assert app.swagger_ui_parameters, "swagger_ui_parameters not set on the FastAPI app"
    assert "docExpansion" in app.swagger_ui_parameters


def test_insight_response_fields_have_descriptions() -> None:
    """Key fields on InsightResponse must carry a description in the schema."""
    schema = _schema()
    insight_schema = schema.get("components", {}).get("schemas", {}).get("InsightResponse", {})
    props = insight_schema.get("properties", {})
    for field in ("resumo", "fonte", "flags", "gerado_em"):
        assert props.get(field, {}).get("description"), (
            f"InsightResponse.{field} is missing a description in the OpenAPI schema"
        )
