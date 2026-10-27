import logging
import sys

from sqlalchemy.exc import StatementError
from database.db import engine
from utils.safe_logging import RedactingFormatter


def test_credentials_are_redacted_after_interpolation_and_traceback():
    secret = "synthetic-private-token-for-tests"
    formatter = RedactingFormatter(logging.Formatter("%(levelname)s %(message)s"), [secret])
    try:
        raise RuntimeError("https://api.example/bot" + secret)
    except RuntimeError:
        record = logging.LogRecord("test", logging.ERROR, __file__, 1, "Request failed: %s", (secret,), sys.exc_info())
    text = formatter.format(record)
    assert secret not in text
    assert "[REDACTED]" in text and "RuntimeError" in text


def test_database_exceptions_hide_submitted_parameter_values():
    assert engine.sync_engine.hide_parameters is True
    error = StatementError("synthetic error", "INSERT INTO members VALUES (?)",
                           ("synthetic-personal-contact",), RuntimeError("invalid"), hide_parameters=True)
    assert "synthetic-personal-contact" not in str(error)
