"""Typed failures raised by the operational store boundary."""


class StoreError(RuntimeError):
    """Base class for operational store failures."""


class StoreNotFoundError(StoreError):
    """The configured operational store or requested durable fact is absent."""


class StoreUnavailableError(StoreError):
    """The operational store exists but cannot currently serve the request."""


class StoreCorruptError(StoreUnavailableError):
    """SQLite reported malformed or corrupt operational data."""


class StoreMigrationRequiredError(StoreUnavailableError):
    """The store exists but lacks schema or facts required by the web contract."""
