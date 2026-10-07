import pytest
from sqlalchemy.orm import Session

from tests.api_contract import find_operation, login
from tests.factories import make_user


@pytest.mark.parametrize(
    ("path", "method", "template"),
    [
        ("/api/stores", "post", "/api/stores"),
        ("/api/stores/abc", "get", "/api/stores/{storeId}"),
        ("/api/stores/abc/manual/draft", "get", "/api/stores/{storeId}/manual/draft"),
        ("/api/stores/abc/manual/media/m1/content", "get", "/api/stores/{storeId}/manual/media/{mediaId}/content"),
        ("/api/auth/session", "get", "/api/auth/session"),
    ],
)
def test_templates_match_literal_segments_first(path, method, template):
    assert find_operation(path, method)[0] == template


@pytest.mark.parametrize(
    ("path", "method"),
    [("/api/stores/abc", "delete"), ("/api/stores/a/b/c/d/e", "get"), ("/api/stores/", "get")],
)
def test_unknown_operations_are_not_matched(path, method):
    assert find_operation(path, method) is None


def test_session_endpoint_contract_through_full_app(api, db_engine):
    with Session(db_engine) as db:
        user_id = make_user(db, "WORKER").id
        db.commit()
    assert api.get("/api/auth/session").status_code == 401
    login(api, user_id)
    assert api.get("/api/auth/session").status_code == 200
