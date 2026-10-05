import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.errors import install_error_handlers
from app.pagination import MAX_PAGE, PageParams, Pagination, page_response

ROWS = list(range(45))


def build_app() -> FastAPI:
    app = FastAPI()
    install_error_handlers(app)

    @app.get("/api/t/list")
    def listing(params: Pagination) -> dict:
        items = ROWS[params.offset: params.offset + params.limit]
        return page_response(items, len(ROWS), params)

    return app


@pytest.fixture
def client():
    return TestClient(build_app())


def fields(response) -> dict:
    return {error["field"]: error["code"] for error in response.json()["fieldErrors"]}


def test_defaults_are_page_zero_and_size_twenty(client):
    body = client.get("/api/t/list").json()
    assert (body["page"], body["size"]) == (0, 20)
    assert body["items"] == ROWS[:20]
    assert body["totalItems"] == 45 and body["totalPages"] == 3


def test_page_is_zero_based(client):
    assert client.get("/api/t/list?page=0&size=10").json()["items"] == ROWS[0:10]
    assert client.get("/api/t/list?page=1&size=10").json()["items"] == ROWS[10:20]
    assert client.get("/api/t/list?page=4&size=10").json()["items"] == ROWS[40:45]


@pytest.mark.parametrize("size", ["1", "100"])
def test_size_bounds_are_inclusive(client, size):
    response = client.get(f"/api/t/list?size={size}")
    assert response.status_code == 200 and response.json()["size"] == int(size)


@pytest.mark.parametrize("size", ["0", "101", "1000", "-1", "-100"])
def test_size_outside_one_to_hundred_is_rejected(client, size):
    response = client.get(f"/api/t/list?size={size}")
    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION_ERROR"
    assert "size" in fields(response)


def test_page_past_the_end_is_an_empty_success(client):
    body = client.get("/api/t/list?page=500&size=10").json()
    assert body["items"] == [] and body["page"] == 500 and body["totalPages"] == 5


@pytest.mark.parametrize("value", [
    "-1", "-0", "abc", "", " ", "1.5", "1.0", "1e2", "0x10", "1_0", "+1", " 1", "1 ", "٣",
    "NaN", "null", "true", "9" * 30, "1" * 10, str(MAX_PAGE + 1), "[1]",
])
def test_invalid_page_is_rejected(client, value):
    response = client.get("/api/t/list", params={"page": value})
    assert response.status_code == 422, value
    assert "page" in fields(response)


@pytest.mark.parametrize("value", ["abc", "", "1.5", "+10", "0x10", "twenty"])
def test_invalid_size_format_is_rejected(client, value):
    response = client.get("/api/t/list", params={"size": value})
    assert response.status_code == 422
    assert fields(response) == {"size": "INVALID_FORMAT"}


def test_out_of_range_and_bad_format_are_distinguished(client):
    assert fields(client.get("/api/t/list?size=101")) == {"size": "OUT_OF_RANGE"}
    assert fields(client.get("/api/t/list?page=x")) == {"page": "INVALID_FORMAT"}


def test_every_invalid_parameter_is_reported_together(client):
    response = client.get("/api/t/list?page=x&size=0")
    assert fields(response) == {"page": "INVALID_FORMAT", "size": "OUT_OF_RANGE"}


def test_duplicate_parameters_are_ambiguous_and_rejected(client):
    response = client.get("/api/t/list?page=1&page=2")
    assert response.status_code == 422 and "page" in fields(response)


def test_largest_allowed_page_is_accepted(client):
    assert client.get(f"/api/t/list?page={MAX_PAGE}").status_code == 200


def test_leading_zeros_are_plain_numbers(client):
    assert client.get("/api/t/list?page=001&size=010").json()["items"] == ROWS[10:20]


def test_unrelated_query_parameters_are_ignored(client):
    assert client.get("/api/t/list?page=1&sort=name").status_code == 200


def test_offset_and_limit():
    assert PageParams(page=3, size=25).offset == 75
    assert PageParams(page=3, size=25).limit == 25


def test_total_pages_rounds_up_and_is_zero_when_empty():
    assert page_response([], 0, PageParams())["totalPages"] == 0
    assert page_response([], 20, PageParams())["totalPages"] == 1
    assert page_response([], 21, PageParams())["totalPages"] == 2
    assert page_response([], 100, PageParams(size=100))["totalPages"] == 1
