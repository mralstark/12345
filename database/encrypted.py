"""Transparent, fail-closed ORM values. Raw SQL is reserved for offline migration."""
from datetime import date

from sqlalchemy import Text
from sqlalchemy.sql import operators
from sqlalchemy.types import TypeDecorator

from utils.data_crypto import DataEncryptionError, decrypt_bytes, encrypt_bytes


class EncryptedValue(TypeDecorator):
    impl = Text
    cache_ok = True

    def __init__(self, domain: str, kind: str = "str", deterministic: bool = False):
        super().__init__()
        if kind not in {"str", "int", "date"}:
            raise ValueError("Unknown encrypted value type")
        self.domain, self.kind, self.deterministic = domain, kind, deterministic

    class Comparator(TypeDecorator.Comparator):
        def operate(self, op, *other, **kwargs):
            allowed = {operators.is_, operators.is_not}
            if self.type.deterministic:
                allowed |= {operators.eq, operators.ne, operators.in_op, operators.not_in_op}
            if op not in allowed:
                raise TypeError("Encrypted fields do not support plaintext SQL search or ordering")
            return super().operate(op, *other, **kwargs)

    comparator_factory = Comparator

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if self.kind == "int":
            if not isinstance(value, int) or isinstance(value, bool) or not -(2**63) <= value < 2**63:
                raise TypeError("Encrypted integer expected")
            payload = str(value)
        elif self.kind == "date":
            if not isinstance(value, date):
                raise TypeError("Encrypted date expected")
            payload = value.isoformat()
        else:
            if not isinstance(value, str):
                raise TypeError("Encrypted text expected")
            payload = value
        return encrypt_bytes(payload.encode("utf-8"), self.domain, deterministic=self.deterministic)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        try:
            plain = decrypt_bytes(value, self.domain, deterministic=self.deterministic).decode("utf-8")
            return int(plain) if self.kind == "int" else date.fromisoformat(plain) if self.kind == "date" else plain
        except (ValueError, UnicodeError):
            raise DataEncryptionError("Invalid authenticated data type") from None
