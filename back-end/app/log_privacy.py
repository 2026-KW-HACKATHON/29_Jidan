"""Keep OAuth query credentials and exception payloads out of application logs."""
import logging


class SafeServerLogs(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if record.name == "uvicorn.access" and isinstance(record.args, tuple) and len(record.args) == 5:
            client, method, target, version, status = record.args
            record.args = (client, method, str(target).split("?", 1)[0], version, status)
        if record.exc_info:
            # DB/HTTP exception messages can embed personal input, codes, URLs or headers.
            record.msg = "Unhandled server error (%s)"
            record.args = (record.exc_info[0].__name__,)
            record.exc_info = None
            record.exc_text = None
        return True


def install_log_privacy() -> None:
    for name in ("uvicorn.access", "uvicorn.error", "jidan.errors"):
        logger = logging.getLogger(name)
        if not any(isinstance(f, SafeServerLogs) for f in logger.filters):
            logger.addFilter(SafeServerLogs())
