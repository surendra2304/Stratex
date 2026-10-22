class TradingException(Exception):
    """Base exception for all trading system errors."""

class DataError(TradingException):
    """Raised when market data is stale, missing, or invalid."""

class NetworkError(TradingException):
    """Raised for socket, HTTP, or DNS failures."""

class ApiError(TradingException):
    """Raised for exchange API rejection or rate limits."""

class ExecutionError(TradingException):
    """Raised when an order fails to execute safely."""

class PortfolioError(TradingException):
    """Raised for math inconsistencies in equity or cash."""

class PersistenceError(TradingException):
    """Raised when saving or loading ledger/portfolio fails."""

class StrategyError(TradingException):
    """Raised when signal generation fails or produces anomalous output."""

class ConfigError(TradingException):
    """Raised when configuration parameters are structurally invalid."""

class SystemError(TradingException):
    """Raised for unknown critical failures like OS out-of-memory."""

class StateCorruptionError(TradingException):
    """Raised when stored JSON state (like active trades) fails schema validation or is malformed."""

class ZeroFillError(TradingException):
    """Raised when an entry order receives a fill quantity of 0."""
