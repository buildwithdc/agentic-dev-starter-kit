"""Data models and exception definitions for the calculator application."""

from dataclasses import dataclass
from enum import Enum
import time


class OperationType(str, Enum):
    """Enumeration of supported mathematical operations."""

    ADD = "add"
    SUBTRACT = "subtract"
    MULTIPLY = "multiply"
    DIVIDE = "divide"
    POWER = "power"
    MODULO = "modulo"
    SQUARE_ROOT = "square_root"


@dataclass(frozen=True)
class CalculationResult:
    """Immutable record of an executed calculation."""

    operation: OperationType
    operands: tuple[float, ...]
    result: float
    timestamp: float = 0.0

    def __post_init__(self) -> None:
        if self.timestamp == 0.0:
            object.__setattr__(self, "timestamp", time.time())


class CalculatorError(Exception):
    """Base exception class for all calculator-related errors."""

    pass


class DivisionByZeroError(CalculatorError):
    """Raised when an attempt is made to divide by zero."""

    pass


class InvalidOperationError(CalculatorError):
    """Raised when an unsupported operation is requested."""

    pass


class InvalidOperandError(CalculatorError):
    """Raised when an operand is mathematically or structurally invalid."""

    pass
