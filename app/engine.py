"""Calculator engine orchestrating calculation logic, expression parsing, and state."""

import ast
import operator
from typing import Callable, Union

from app.models import (
    CalculationResult,
    CalculatorError,
    DivisionByZeroError,
    InvalidOperandError,
    InvalidOperationError,
    OperationType,
)
from app import operations


_OPERATOR_MAP: dict[type[ast.operator], tuple[Callable[[float, float], float], OperationType]] = {
    ast.Add: (operations.add, OperationType.ADD),
    ast.Sub: (operations.subtract, OperationType.SUBTRACT),
    ast.Mult: (operations.multiply, OperationType.MULTIPLY),
    ast.Div: (operations.divide, OperationType.DIVIDE),
    ast.Pow: (operations.power, OperationType.POWER),
    ast.Mod: (operations.modulo, OperationType.MODULO),
}


class CalculatorEngine:
    """Stateful calculation engine supporting arithmetic, safe expression eval, and memory."""

    def __init__(self) -> None:
        self._history: list[CalculationResult] = []
        self._memory: float = 0.0

    def calculate(
        self,
        operation: OperationType,
        a: float,
        b: float | None = None,
    ) -> CalculationResult:
        """Execute a mathematical operation and record in history.

        Args:
            operation: The operation to execute.
            a: The first operand.
            b: The second operand (required for binary operations).

        Returns:
            CalculationResult containing the executed details and output.

        Raises:
            InvalidOperandError: If required operand is missing.
            InvalidOperationError: If operation is unsupported.
        """
        result_value: float
        operands: tuple[float, ...]

        if operation == OperationType.SQUARE_ROOT:
            operands = (float(a),)
            result_value = operations.square_root(float(a))
        else:
            if b is None:
                raise InvalidOperandError(f"Operation '{operation.value}' requires two operands.")
            operands = (float(a), float(b))
            if operation == OperationType.ADD:
                result_value = operations.add(float(a), float(b))
            elif operation == OperationType.SUBTRACT:
                result_value = operations.subtract(float(a), float(b))
            elif operation == OperationType.MULTIPLY:
                result_value = operations.multiply(float(a), float(b))
            elif operation == OperationType.DIVIDE:
                result_value = operations.divide(float(a), float(b))
            elif operation == OperationType.POWER:
                result_value = operations.power(float(a), float(b))
            elif operation == OperationType.MODULO:
                result_value = operations.modulo(float(a), float(b))
            else:
                raise InvalidOperationError(f"Unsupported operation: {operation}")

        calc_result = CalculationResult(
            operation=operation,
            operands=operands,
            result=result_value,
        )
        self._history.append(calc_result)
        return calc_result

    def evaluate_expression(self, expression: str) -> float:
        """Safely evaluate an arithmetic mathematical expression.

        Args:
            expression: String representation of math expression (e.g. '3 + 4 * 2').

        Returns:
            Computed float result.

        Raises:
            InvalidOperandError: If expression is malformed or contains unsupported syntax.
        """
        trimmed = expression.strip()
        if not trimmed:
            raise InvalidOperandError("Cannot evaluate empty expression.")

        try:
            tree = ast.parse(trimmed, mode="eval")
        except SyntaxError as exc:
            raise InvalidOperandError(f"Invalid mathematical expression syntax: {exc.msg}") from exc

        result = self._eval_node(tree.body)
        return result

    def _eval_node(self, node: ast.AST) -> float:
        """Recursively evaluate an AST node safely."""
        if isinstance(node, ast.Constant):
            if isinstance(node.value, (int, float)):
                return float(node.value)
            raise InvalidOperandError(f"Unsupported constant value type: {type(node.value)}")

        if isinstance(node, ast.UnaryOp):
            operand_val = self._eval_node(node.operand)
            if isinstance(node.op, ast.UAdd):
                return +operand_val
            if isinstance(node.op, ast.USub):
                return -operand_val
            raise InvalidOperandError(f"Unsupported unary operator: {type(node.op).__name__}")

        if isinstance(node, ast.BinOp):
            left_val = self._eval_node(node.left)
            right_val = self._eval_node(node.right)
            op_type = type(node.op)

            if op_type in _OPERATOR_MAP:
                func, op_enum = _OPERATOR_MAP[op_type]
                res = func(left_val, right_val)
                self._history.append(
                    CalculationResult(
                        operation=op_enum,
                        operands=(left_val, right_val),
                        result=res,
                    )
                )
                return res
            raise InvalidOperandError(f"Unsupported binary operator: {op_type.__name__}")

        raise InvalidOperandError(f"Unsupported expression element: {type(node).__name__}")

    def get_history(self) -> list[CalculationResult]:
        """Return a copy of the calculation history."""
        return list(self._history)

    def clear_history(self) -> None:
        """Clear all stored calculation history."""
        self._history.clear()

    @property
    def memory(self) -> float:
        """Current value stored in calculator memory."""
        return self._memory

    def memory_store(self, value: float) -> None:
        """Store a value into memory.

        Args:
            value: Float number to store.
        """
        self._memory = float(value)

    def memory_recall(self) -> float:
        """Recall the current value stored in memory."""
        return self._memory

    def memory_clear(self) -> None:
        """Reset memory to 0.0."""
        self._memory = 0.0

    def memory_add(self, value: float) -> None:
        """Add a value to the current memory.

        Args:
            value: Number to add to memory.
        """
        self._memory += float(value)
