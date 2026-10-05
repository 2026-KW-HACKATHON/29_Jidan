import logging

from app.log_privacy import SafeServerLogs


def test_uvicorn_access_never_logs_oauth_query():
    record = logging.LogRecord("uvicorn.access", 20, "", 1, '%s - "%s %s HTTP/%s" %d',
                               ("client", "GET", "/api/auth/google/callback?code=SECRET&state=STATE", "1.1", 302), None)
    assert SafeServerLogs().filter(record)
    assert "SECRET" not in record.getMessage() and "STATE" not in record.getMessage()
    assert "/api/auth/google/callback" in record.getMessage()


def test_exception_payloads_are_removed():
    try:
        raise RuntimeError("secret-token-and-email")
    except RuntimeError as exc:
        record = logging.LogRecord("uvicorn.error", 40, "", 1, "Failed", (), (type(exc), exc, exc.__traceback__))
    SafeServerLogs().filter(record)
    assert record.exc_info is None and "secret-token-and-email" not in record.getMessage()
    assert "RuntimeError" in record.getMessage()


def test_httpx_logging_does_not_reveal_address(caplog):
    import httpx

    from app.log_privacy import install_log_privacy
    install_log_privacy()
    caplog.set_level(logging.INFO, logger="httpx")
    with httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200))) as client:
        client.get("https://dapi.kakao.com/v2/local/search/address.json?query=PRIVATE-ADDRESS")
    assert "PRIVATE-ADDRESS" not in caplog.text
    assert "search/address.json" in caplog.text
