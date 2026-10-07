#!/usr/bin/env python3
"""Dynamic CLI for Rally API.

Usage:
    rally <method> [args...]

Examples:
    rally get portfolioitem/feature '{"query":"(FormattedID = F12345)"}' true
    rally query_features '{"Name":"My Feature"}'
    rally get_object /portfolioitem/feature/12345 'Name,FormattedID,State'
    rally update_object /hierarchicalrequirement/67890 '{"State":"Completed"}' false

Arguments are automatically parsed as:
    - JSON objects/arrays (if they start with { or [)
    - Booleans (true/false, True/False, yes/no)
    - Numbers (integers and floats)
    - null/None (null, None)
    - Strings (everything else)
"""

import sys
import json
import inspect
from typing import Any
from rally_api import RallyAPI

# Ensure stdout/stderr use UTF-8 on Windows (prevents charmap errors for ✓ etc.)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")


def parse_arg(arg: str) -> Any:
    """
    Parse a command line argument into its appropriate Python type.

    Args:
        arg: String argument from command line

    Returns:
        Parsed value (dict, list, bool, int, float, None, or str)
    """
    # Handle JSON objects and arrays
    if arg.startswith('{') or arg.startswith('['):
        try:
            return json.loads(arg)
        except json.JSONDecodeError:
            return arg

    # Handle booleans
    if arg.lower() in ('true', 'yes', '1'):
        return True
    if arg.lower() in ('false', 'no', '0'):
        return False

    # Handle None/null
    if arg.lower() in ('none', 'null'):
        return None

    # Handle numbers
    try:
        # Try integer first
        if '.' not in arg and 'e' not in arg.lower():
            return int(arg)
        # Then float
        return float(arg)
    except ValueError:
        pass

    # Default to string
    return arg


def print_result(result: Any) -> None:
    """Pretty print the result."""
    if isinstance(result, (dict, list)):
        print(json.dumps(result, indent=2, default=str))
    elif result is None:
        print("[OK] Success (no return value)")
    else:
        print(result)


def get_method_signature(method) -> str:
    """Get a human-readable method signature."""
    try:
        sig = inspect.signature(method)
        params = []
        for name, param in sig.parameters.items():
            if name == 'self':
                continue

            # Build parameter string
            param_str = name
            if param.annotation != inspect.Parameter.empty:
                param_str += f": {param.annotation.__name__ if hasattr(param.annotation, '__name__') else param.annotation}"
            if param.default != inspect.Parameter.empty:
                param_str += f" = {param.default}"

            params.append(param_str)

        return f"{method.__name__}({', '.join(params)})"
    except Exception:
        return f"{method.__name__}(...)"


def print_all_methods() -> None:
    """Print module usage and all available RallyAPI methods."""
    print(__doc__)
    print("\nAvailable methods on RallyAPI class:")
    print("=" * 60)

    client = RallyAPI()
    methods = [name for name in dir(client) if not name.startswith('_') and callable(getattr(client, name))]

    for method_name in sorted(methods):
        method = getattr(client, method_name)
        sig = get_method_signature(method)

        doc_summary = ""
        if method.__doc__:
            doc_lines = [line.strip() for line in method.__doc__.strip().split('\n') if line.strip()]
            doc_summary = doc_lines[0] if doc_lines else ""

        print(f"  {sig}")
        if doc_summary:
            print(f"    → {doc_summary}")
        print()


def print_method_help(client, method_name: str) -> None:
    """Print full signature and docstring for a single method."""
    method = getattr(client, method_name)
    print(f"rally {get_method_signature(method)}\n")
    if method.__doc__:
        print(method.__doc__.strip())
    else:
        print("(no documentation available)")


def main():
    """Main CLI entry point."""
    if len(sys.argv) < 2 or sys.argv[1] in ('--help', '-h'):
        print_all_methods()
        sys.exit(0 if len(sys.argv) >= 2 else 1)

    method_name = sys.argv[1]
    args = [a for a in sys.argv[2:] if a not in ('--help', '-h')]
    show_help = '--help' in sys.argv[2:] or '-h' in sys.argv[2:]

    try:
        # Initialize Rally API client
        client = RallyAPI()

        # Get the method
        if not hasattr(client, method_name):
            print(f"[ERROR] Method '{method_name}' not found on RallyAPI class")
            print(f"\nDid you mean one of these?")

            # Find similar method names
            methods = [name for name in dir(client) if not name.startswith('_') and callable(getattr(client, name))]
            similar = [m for m in methods if method_name.lower() in m.lower()]

            for method in similar[:5]:
                print(f"  - {method}")

            sys.exit(1)

        method = getattr(client, method_name)

        # Verify it's callable
        if not callable(method):
            print(f"[ERROR] '{method_name}' is not a callable method")
            sys.exit(1)

        # Show per-method help and exit
        if show_help:
            print_method_help(client, method_name)
            sys.exit(0)

        # Parse arguments
        parsed_args = [parse_arg(arg) for arg in args]

        # Show what we're calling (helpful for debugging)
        print(f"Calling: {method_name}(", end="")
        for i, arg in enumerate(parsed_args):
            if i > 0:
                print(", ", end="")
            if isinstance(arg, str):
                print(f"'{arg}'", end="")
            else:
                print(f"{arg}", end="")
        print(")\n")

        # Call the method
        result = method(*parsed_args)

        # Print the result
        print_result(result)

    except TypeError as e:
        print(f"[ERROR] Argument Error: {e}")
        print(f"\nMethod signature:")
        method = getattr(client, method_name)
        print(f"  {get_method_signature(method)}")

        # Show docstring if available
        if method.__doc__:
            print(f"\nDocumentation:")
            print(method.__doc__)

        sys.exit(1)
    except Exception as e:
        print(f"[ERROR] {e}")

        # Show full traceback in debug mode
        if '--debug' in sys.argv:
            import traceback
            print("\nFull traceback:")
            traceback.print_exc()

        sys.exit(1)


if __name__ == "__main__":
    main()
