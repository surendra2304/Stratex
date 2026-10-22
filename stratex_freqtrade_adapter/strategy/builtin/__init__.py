"""stratex_freqtrade_adapter/strategy/builtin/__init__.py"""

from .sample_strategy import SampleFreqtradeStrategy
from .bband_rsi import BbandRsiStrategy
from .awesome_macd import AwesomeMacdStrategy

__all__ = [
    "SampleFreqtradeStrategy",
    "BbandRsiStrategy",
    "AwesomeMacdStrategy",
]
