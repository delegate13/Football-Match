class MonitorError(Exception):
    """Base exception for expected monitor failures."""


class ConfigurationError(MonitorError):
    """Raised when the external configuration is invalid."""


class ESPNAPIError(MonitorError):
    """Raised when ESPN cannot provide a valid scoreboard response."""


class DataValidationError(MonitorError):
    """Raised when scoreboard data does not match the expected structure."""
