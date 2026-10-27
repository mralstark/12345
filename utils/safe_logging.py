"""Redact configured credentials after formatting, including tracebacks."""
import logging


class RedactingFormatter(logging.Formatter):
    def __init__(self, original, secrets):
        super().__init__()
        self.original = original or logging.Formatter()
        self.secrets = tuple(sorted({s for s in secrets if s and len(s) >= 8}, key=len, reverse=True))

    def format(self, record):
        text = self.original.format(record)
        for secret in self.secrets:
            text = text.replace(secret, "[REDACTED]")
        return text


def install_secret_redaction():
    import config
    secrets = [getattr(config, name, "") for name in (
        "BOT_TOKEN", "VK_MINI_APP_SECRET", "MEDIA_SIGNING_KEY", "DATABASE_URL", "BOT_PROXY_URL",
    )]
    loggers = [logging.getLogger(), *[value for value in logging.Logger.manager.loggerDict.values()
                                    if isinstance(value, logging.Logger)]]
    for logger in loggers:
        for handler in logger.handlers:
            if not isinstance(handler.formatter, RedactingFormatter):
                handler.setFormatter(RedactingFormatter(handler.formatter, secrets))
