"""Named exception types let callers handle expected failures consistently."""

class MonitorError(Exception):
    """Base class for expected errors that the command line can report cleanly."""


class ConfigurationError(MonitorError):
    """Raised when config.json is missing, malformed, or contains invalid values."""


class ESPNAPIError(MonitorError):
    """Raised when an ESPN request and any usable saved response both fail."""


class DataValidationError(MonitorError):
    """Raised when saved or downloaded data has an unexpected structure."""
