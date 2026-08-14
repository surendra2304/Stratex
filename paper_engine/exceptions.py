class TradingException(Exception):
    """Base exception for all trading system errors."""
    pass

class DataError(TradingException):
    """Raised when market data is stale, missing, or invalid."""
    pass

class NetworkError(TradingException):
    """Raised for socket, HTTP, or DNS failures."""
    pass

class ApiError(TradingException):
    """Raised for exchange API rejection or rate limits."""
    pass

class ExecutionError(TradingException):
    """Raised when an order fails to execute safely."""
    pass

class PortfolioError(TradingException):
    """Raised for math inconsistencies in equity or cash."""
    pass

class PersistenceError(TradingException):
    """Raised when saving or loading ledger/portfolio fails."""
    pass

class StrategyError(TradingException):
    """Raised when signal generation fails or produces anomalous output."""
    pass

class ConfigError(TradingException):
    """Raised when configuration parameters are structurally invalid."""
    pass

class SystemError(TradingException):
    """Raised for unknown critical failures like OS out-of-memory."""
    pass

class StateCorruptionError(TradingException):
    """Raised when stored JSON state (like active trades) fails schema validation or is malformed."""
    pass
