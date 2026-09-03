"""Core arithmetic functions for the calculator."""

import math
from app.models import DivisionByZeroError, InvalidOperandError


def add(a: float, b: float) -> float:
    """Add two numbers.

    Args:
        a: First operand.
        b: Second operand.

    Returns:
        The sum of a and b.
    """
    return float(a + b)


def subtract(a: float, b: float) -> float:
    """Subtract the second number from the first number.

    Args:
        a: Minuend.
        b: Subtrahend.

    Returns:
        The difference between a and b.
    """
    return float(a - b)


def multiply(a: float, b: float) -> float:
    """Multiply two numbers.

    Args:
        a: First factor.
        b: Second factor.

    Returns:
        The product of a and b.
    """
    return float(a * b)


def divide(a: float, b: float) -> float:
    """Divide the first number by the second number.

    Args:
        a: Dividend.
        b: Divisor.

    Returns:
        The quotient of a and b.

    Raises:
        DivisionByZeroError: If b is zero.
    """
    if b == 0.0:
        raise DivisionByZeroError("Cannot divide by zero.")
    return float(a / b)


def power(base: float, exponent: float) -> float:
    """Raise a base to an exponent power.

    Args:
        base: The base number.
        exponent: The exponent power.

    Returns:
        The base raised to the power of exponent.

    Raises:
        InvalidOperandError: If calculation produces an invalid result (e.g., negative base with fractional power).
    """
    try:
        return float(math.pow(base, exponent))
    except ValueError as exc:
        raise InvalidOperandError(f"Invalid power operation: {exc}") from exc


def modulo(a: float, b: float) -> float:
    """Compute the remainder of division of a by b.

    Args:
        a: Dividend.
        b: Divisor.

    Returns:
        The remainder of a divided by b.

    Raises:
        DivisionByZeroError: If b is zero.
    """
    if b == 0.0:
        raise DivisionByZeroError("Cannot calculate modulo with divisor of zero.")
    return float(a % b)


def square_root(a: float) -> float:
    """Calculate the square root of a non-negative number.

    Args:
        a: Non-negative number.

    Returns:
        The principal square root of a.

    Raises:
        InvalidOperandError: If a is negative.
    """
    if a < 0.0:
        raise InvalidOperandError(f"Cannot calculate square root of negative number: {a}")
    return float(math.sqrt(a))
