"""Map common CCXT exception classes to stable Stratex categories."""

class CCXTErrorMapper:
    @staticmethod
    def classify(exc: Exception) -> str:
        name = exc.__class__.__name__.lower()
        if "authentication" in name:
            return "AUTHENTICATION_ERROR"
        if "ratelimit" in name or "ddos" in name:
            return "RATE_LIMIT"
        if "timeout" in name or "network" in name or "exchangeunavailable" in name:
            return "NETWORK_ERROR"
        if "invalidorder" in name:
            return "INVALID_ORDER"
        if "badsymbol" in name:
            return "BAD_SYMBOL"
        if "notsupported" in name:
            return "NOT_SUPPORTED"
        if "badrequest" in name:
            return "BAD_REQUEST"
        return "EXCHANGE_ERROR"
