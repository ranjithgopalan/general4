#!/usr/bin/env python3
"""
Test helper functions for Claude CLI integration testing.
"""

import subprocess
import json
import os
from typing import Dict, List, Optional, Tuple


class ClaudeTestHelper:
    """Helper class for testing Claude CLI queries."""

    def __init__(self, claude_path: str = "/Users/christopher.le/.local/bin/claude"):
        """Initialize test helper.

        Args:
            claude_path: Path to Claude CLI executable
        """
        self.claude_path = claude_path
        self.last_result: Optional[Dict] = None
        # Environment variables for Claude
        # Use API key from environment, fallback to existing key for compatibility
        api_key = os.environ.get("ANTHROPIC_API_KEY", os.environ.get("ANTHROPIC_API_KEY_DEFAULT", ""))
        self.env = {
            **os.environ,
            "CLAUDE_CODE_DISABLE_BACKGROUND_TASKS": "1",
        }
        if api_key:
            self.env["ANTHROPIC_API_KEY"] = api_key

    def run_query(self, query: str, verbose: bool = True) -> bool:
        """Run a Claude query and stream output, checking for errors.

        Args:
            query: The query to send to Claude
            verbose: Whether to print turn-by-turn output

        Returns:
            True if query succeeded without errors, False otherwise
        """
        # Build command with stream-json output to see all turns
        # Use --dangerously-skip-permissions to allow tool execution without prompts
        cmd = [
            self.claude_path,
            "--dangerously-skip-permissions",
            "--verbose",
            "--output-format", "stream-json",
            "-p",
            query
        ]

        if verbose:
            print(f"Query: {query}")
            print("=" * 80)

        # Run command with environment variables and capture output
        # Add 60 second timeout to prevent tests from hanging
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, env=self.env, timeout=60)
        except subprocess.TimeoutExpired:
            if verbose:
                print(f"\n✗ FAILED: Query timed out after 60 seconds")
            return False

        # Check for immediate errors
        if result.returncode != 0 and not result.stdout:
            if verbose:
                print(f"\n✗ FAILED: Command failed with return code {result.returncode}")
                if result.stderr:
                    print(f"Error output:\n{result.stderr}")
            return False

        # Parse and analyze turns
        analysis = self._analyze_turns(result.stdout, verbose)
        self.last_result = analysis

        # Return success status
        success = not analysis["has_error"] and result.returncode == 0
        if verbose:
            self._print_summary(analysis, success)

        return success

    def _analyze_turns(self, stdout: str, verbose: bool) -> Dict:
        """Analyze turn-by-turn output from Claude.

        Args:
            stdout: JSON stream output from Claude
            verbose: Whether to print details

        Returns:
            Dictionary with analysis results
        """
        turn_number = 0
        has_error = False
        errors = []
        tool_uses = []
        final_result = None
        consecutive_errors = 0
        MAX_CONSECUTIVE_ERRORS = 4

        for line in stdout.strip().split('\n'):
            if not line:
                continue

            try:
                event = json.loads(line)
                event_type = event.get("type")

                if event_type == "assistant":
                    # Claude's turn - check for tool uses
                    message = event.get("message", {})
                    content = message.get("content", [])

                    for item in content:
                        if item.get("type") == "text":
                            turn_number += 1
                            text = item['text']
                            if verbose:
                                print(f"\n⏺ Turn {turn_number}: {text[:100]}...")
                        elif item.get("type") == "tool_use":
                            tool_name = item.get("name")
                            tool_uses.append(tool_name)
                            if verbose:
                                print(f"  → Using tool: {tool_name}")

                elif event_type == "user":
                    # Tool result
                    message = event.get("message", {})
                    content = message.get("content", [])

                    turn_had_error = False
                    for item in content:
                        if item.get("type") == "tool_result":
                            tool_result = event.get("tool_use_result", {})
                            is_error = item.get("is_error", False) or tool_result.get("isImage") == "error"

                            if is_error:
                                error_content = item.get("content", "")
                                if isinstance(error_content, str):
                                    errors.append(error_content[:200])
                                    if verbose:
                                        print(f"  ✗ ERROR in tool result")
                                        print(f"    {error_content[:200]}")
                                turn_had_error = True
                            else:
                                stdout_text = tool_result.get("stdout", "")
                                stderr_text = tool_result.get("stderr", "")
                                if verbose:
                                    if stdout_text:
                                        print(f"  ✓ Tool output: {stdout_text[:100]}...")
                                    if stderr_text:
                                        print(f"  ⚠ Stderr: {stderr_text[:100]}")

                    # Track consecutive errors
                    if turn_had_error:
                        consecutive_errors += 1
                        if verbose:
                            print(f"  (Consecutive errors: {consecutive_errors}/{MAX_CONSECUTIVE_ERRORS})")
                        if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                            has_error = True
                            if verbose:
                                print(f"  ✗ FAILED: {MAX_CONSECUTIVE_ERRORS} consecutive errors")
                    else:
                        # Reset counter on successful tool use
                        if consecutive_errors > 0 and verbose:
                            print(f"  (Recovered from errors)")
                        consecutive_errors = 0

                elif event_type == "result":
                    # Final result
                    final_result = event.get("result", "")
                    success = event.get("subtype") == "success"

                    if verbose:
                        print(f"\n{'=' * 80}")
                        print(f"Final result ({event.get('subtype')}):")
                        print(final_result[:300])

                    if not success:
                        has_error = True

            except json.JSONDecodeError:
                # Skip non-JSON lines
                continue

        return {
            "turn_count": turn_number,
            "has_error": has_error,
            "errors": errors,
            "tool_uses": tool_uses,
            "final_result": final_result,
            "consecutive_errors": consecutive_errors
        }

    def _print_summary(self, analysis: Dict, success: bool):
        """Print test summary.

        Args:
            analysis: Analysis results from _analyze_turns
            success: Whether test was successful
        """
        if success:
            print(f"\n✓ SUCCESS: Query completed without errors")
            print(f"  Turns: {analysis['turn_count']}")
            print(f"  Tools used: {', '.join(analysis['tool_uses']) if analysis['tool_uses'] else 'None'}")
        else:
            print(f"\n✗ FAILED: Query encountered errors")
            if analysis['errors']:
                print(f"  Errors: {len(analysis['errors'])}")
                for i, error in enumerate(analysis['errors'][:3], 1):
                    print(f"    {i}. {error}")

    def assert_query_success(self, query: str, message: str = None):
        """Run query and assert it succeeds.

        Args:
            query: Query to run
            message: Optional assertion message

        Raises:
            AssertionError: If query fails
        """
        success = self.run_query(query, verbose=True)
        if not success:
            error_msg = message or f"Query failed: {query}"
            raise AssertionError(error_msg)

    def assert_no_errors(self, query: str):
        """Run query and assert no errors occurred.

        Args:
            query: Query to run

        Raises:
            AssertionError: If any errors detected
        """
        success = self.run_query(query, verbose=False)
        if not success:
            errors = self.last_result.get("errors", []) if self.last_result else []
            error_details = "\n".join(errors) if errors else "Unknown error"
            raise AssertionError(f"Query had errors:\n{error_details}")

    def assert_tool_used(self, query: str, tool_name: str):
        """Run query and assert specific tool was used.

        Args:
            query: Query to run
            tool_name: Name of tool that should have been used

        Raises:
            AssertionError: If tool was not used
        """
        self.run_query(query, verbose=False)
        if not self.last_result:
            raise AssertionError("No result available")

        tool_uses = self.last_result.get("tool_uses", [])
        if tool_name not in tool_uses:
            raise AssertionError(
                f"Tool '{tool_name}' was not used. "
                f"Tools used: {', '.join(tool_uses) if tool_uses else 'None'}"
            )

    def get_last_result(self) -> Optional[Dict]:
        """Get analysis results from last query.

        Returns:
            Dictionary with turn_count, has_error, errors, tool_uses, final_result
        """
        return self.last_result


# Convenience function for simple usage
def run_query(query: str, verbose: bool = True) -> bool:
    """Run a Claude query and check for errors.

    Args:
        query: The query to send to Claude
        verbose: Whether to print turn-by-turn output

    Returns:
        True if query succeeded without errors, False otherwise
    """
    helper = ClaudeTestHelper()
    return helper.run_query(query, verbose)
