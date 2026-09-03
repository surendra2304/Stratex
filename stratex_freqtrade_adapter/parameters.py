"""Small Stratex-native hyperparameter model inspired by Freqtrade."""

from dataclasses import dataclass
from typing import Any, Sequence

@dataclass
class BaseParameter:
    default: Any
    space: str = "strategy"
    optimize: bool = True
    load: bool = True

    def __post_init__(self):
        self.value = self.default

    def suggest(self, trial: Any, name: str) -> Any:
        raise NotImplementedError


class IntParameter(BaseParameter):
    def __init__(
        self,
        low: int,
        high: int,
        *,
        default: int,
        space: str = "strategy",
        optimize: bool = True,
        load: bool = True,
        step: int = 1,
    ):
        if low > high:
            raise ValueError("low must be <= high")
        super().__init__(default=default, space=space, optimize=optimize, load=load)
        self.low, self.high = int(low), int(high)
        self.step = int(step)

    @property
    def range(self) -> range:
        return range(self.low, self.high + 1, self.step) if self.optimize else range(self.value, self.value + 1)

    def suggest(self, trial: Any, name: str) -> int:
        if not self.optimize or trial is None:
            return self.value
        return trial.suggest_int(name, self.low, self.high, step=self.step)

    def to_dict(self) -> dict:
        return {
            "type": "int",
            "low": self.low,
            "high": self.high,
            "default": self.default,
            "value": self.value,
            "step": self.step,
            "optimize": self.optimize,
        }


class RealParameter(BaseParameter):
    def __init__(
        self,
        low: float,
        high: float,
        *,
        default: float,
        space: str = "strategy",
        optimize: bool = True,
        load: bool = True,
        step: float | None = None,
    ):
        if low > high:
            raise ValueError("low must be <= high")
        super().__init__(default=default, space=space, optimize=optimize, load=load)
        self.low, self.high = float(low), float(high)
        self.step = float(step) if step is not None else None

    def suggest(self, trial: Any, name: str) -> float:
        if not self.optimize or trial is None:
            return self.value
        if self.step is not None:
            return trial.suggest_float(name, self.low, self.high, step=self.step)
        return trial.suggest_float(name, self.low, self.high)

    def to_dict(self) -> dict:
        return {
            "type": "real",
            "low": self.low,
            "high": self.high,
            "default": self.default,
            "value": self.value,
            "step": self.step,
            "optimize": self.optimize,
        }


class CategoricalParameter(BaseParameter):
    def __init__(
        self,
        choices: Sequence[Any],
        *,
        default: Any = None,
        space: str = "strategy",
        optimize: bool = True,
        load: bool = True,
    ):
        choices = list(choices)
        if not choices:
            raise ValueError("choices cannot be empty")
        if default is None:
            default = choices[0]
        if default not in choices:
            raise ValueError("default must be one of choices")
        super().__init__(default=default, space=space, optimize=optimize, load=load)
        self.choices = choices

    def suggest(self, trial: Any, name: str) -> Any:
        if not self.optimize or trial is None:
            return self.value
        return trial.suggest_categorical(name, self.choices)

    def to_dict(self) -> dict:
        return {
            "type": "categorical",
            "choices": self.choices,
            "default": self.default,
            "value": self.value,
            "optimize": self.optimize,
        }

