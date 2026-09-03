"""Command-line interface for the calculator application."""

import argparse
import sys
from app.engine import CalculatorEngine
from app.models import CalculatorError, OperationType


def parse_arguments(args: list[str] | None = None) -> argparse.Namespace:
    """Parse command line arguments.

    Args:
        args: List of argument strings (defaults to sys.argv[1:]).

    Returns:
        Parsed arguments namespace.
    """
    parser = argparse.ArgumentParser(
        prog="calculator",
        description="A robust and extensible CLI Calculator Application.",
    )
    parser.add_argument(
        "-e",
        "--eval",
        dest="expression",
        type=str,
        help="Evaluate a mathematical expression (e.g., '12 + 4 * 3').",
    )
    parser.add_argument(
        "--op",
        dest="operation",
        choices=[op.value for op in OperationType],
        help="Perform a direct operation.",
    )
    parser.add_argument(
        "operands",
        nargs="*",
        type=float,
        help="Operands for direct operation.",
    )
    parser.add_argument(
        "-i",
        "--interactive",
        action="store_true",
        help="Start interactive calculation session.",
    )
    return parser.parse_args(args)


def run_interactive(engine: CalculatorEngine) -> None:
    """Run an interactive calculator REPL session.

    Args:
        engine: The CalculatorEngine instance to use.
    """
    print("=== Calculator Interactive Session ===")
    print("Type mathematical expressions to calculate, 'history' to view log, or 'exit' / 'quit' to quit.\n")
    while True:
        try:
            user_input = input("calc> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nExiting calculator.")
            break

        if not user_input:
            continue
        if user_input.lower() in ("exit", "quit"):
            print("Goodbye!")
            break
        if user_input.lower() == "history":
            history = engine.get_history()
            if not history:
                print("No history recorded.")
            else:
                for idx, item in enumerate(history, 1):
                    print(f"  {idx}. {item.operation.value}{item.operands} = {item.result}")
            continue
        if user_input.lower() == "clear":
            engine.clear_history()
            print("History cleared.")
            continue

        try:
            result = engine.evaluate_expression(user_input)
            print(f"= {result}")
        except CalculatorError as exc:
            print(f"Error: {exc}")


def main(args: list[str] | None = None) -> int:
    """Main entrypoint for CLI calculator.

    Args:
        args: Optional list of argument strings.

    Returns:
        Exit code (0 for success, non-zero for error).
    """
    parsed = parse_arguments(args)
    engine = CalculatorEngine()

    if parsed.expression:
        try:
            result = engine.evaluate_expression(parsed.expression)
            print(f"{result}")
            return 0
        except CalculatorError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1

    if parsed.operation:
        op_enum = OperationType(parsed.operation)
        if op_enum == OperationType.SQUARE_ROOT:
            if len(parsed.operands) != 1:
                print("Error: square_root requires exactly 1 operand.", file=sys.stderr)
                return 1
            try:
                res = engine.calculate(op_enum, parsed.operands[0])
                print(f"{res.result}")
                return 0
            except CalculatorError as exc:
                print(f"Error: {exc}", file=sys.stderr)
                return 1
        else:
            if len(parsed.operands) != 2:
                print(f"Error: {parsed.operation} requires exactly 2 operands.", file=sys.stderr)
                return 1
            try:
                res = engine.calculate(op_enum, parsed.operands[0], parsed.operands[1])
                print(f"{res.result}")
                return 0
            except CalculatorError as exc:
                print(f"Error: {exc}", file=sys.stderr)
                return 1

    if parsed.interactive or len(sys.argv) == 1:
        run_interactive(engine)
        return 0

    return 0


if __name__ == "__main__":
    sys.exit(main())
