#!/usr/bin/env python3
"""
Rally Template CLI

Command-line interface for Rally work item template operations.
Supports copying, listing, and managing templates.

Usage:
    # Copy all templates to .claude/.fe-sm-templates/
    python template_cli.py copy

    # Copy specific template
    python template_cli.py copy --type story

    # Reset all templates
    python template_cli.py copy --reset

    # List available templates
    python template_cli.py list

    # Render a template with variables
    python template_cli.py render --type story --vars '{"role":"developer","want":"implement feature","benefit":"users benefit"}'
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Dict

# Use relative imports since we're inside the rally_api package
from ..template_loader import TemplateLoader, copy_default_templates


def copy_templates(args) -> Dict:
    """Copy default templates to .claude/.fe-sm-templates/"""
    try:
        # Get or create target directory
        target_dir = Path.cwd() / ".claude" / ".fe-sm-templates"

        # Handle reset flag
        if args.reset:
            if target_dir.exists():
                # Remove existing templates
                for template_file in target_dir.glob("*.md"):
                    template_file.unlink()
                message = "Reset templates"
            else:
                message = "Created templates"
        else:
            message = "Copied templates"

        # Copy templates
        results = copy_default_templates(target_dir)

        # Handle type filter
        if args.type:
            template_filename = TemplateLoader.TEMPLATE_MAP.get(args.type.lower())
            if template_filename:
                # Filter to only show requested type
                filtered_results = {
                    k: v for k, v in results.items() if k == template_filename
                }
                results = filtered_results
            else:
                return {
                    "error": f"Unknown template type: {args.type}. Valid types: {', '.join(TemplateLoader.TEMPLATE_MAP.keys())}"
                }

        return {
            "success": True,
            "message": message,
            "target_dir": str(target_dir),
            "results": results,
        }

    except Exception as e:
        return {"error": str(e)}


def list_templates(args) -> Dict:
    """List available templates and their sources."""
    try:
        loader = TemplateLoader()

        # Get available templates
        templates = loader.list_available_templates()

        # Get paths
        custom_dir = loader.get_custom_templates_dir()
        default_dir = loader.get_default_templates_dir()

        return {
            "success": True,
            "templates": templates,
            "custom_templates_dir": str(custom_dir) if custom_dir else None,
            "default_templates_dir": str(default_dir),
            "has_custom_templates": loader.has_custom_templates(),
        }

    except Exception as e:
        return {"error": str(e)}


def render_template(args) -> Dict:
    """Render a template with variables."""
    try:
        loader = TemplateLoader()

        # Parse variables
        variables = {}
        if args.vars:
            try:
                variables = json.loads(args.vars)
            except json.JSONDecodeError as e:
                return {"error": f"Invalid JSON in --vars: {str(e)}"}

        # Render template
        rendered = loader.render_template(args.type, variables)

        if rendered is None:
            return {"error": f"Template not found for type: {args.type}"}

        # Get template path info
        template_path = loader.get_template_path(args.type)
        is_custom = (
            loader.custom_templates_dir
            and template_path
            and template_path.parent == loader.custom_templates_dir
        )

        return {
            "success": True,
            "type": args.type,
            "template_path": str(template_path) if template_path else None,
            "source": "custom" if is_custom else "default",
            "rendered": rendered,
        }

    except Exception as e:
        return {"error": str(e)}


def show_info(args) -> Dict:
    """Show template system information."""
    try:
        loader = TemplateLoader()

        info = {
            "success": True,
            "default_templates_dir": str(loader.get_default_templates_dir()),
            "custom_templates_dir": str(loader.get_custom_templates_dir())
            if loader.get_custom_templates_dir()
            else None,
            "has_custom_templates": loader.has_custom_templates(),
            "available_types": list(TemplateLoader.TEMPLATE_MAP.keys()),
            "template_files": list(TemplateLoader.TEMPLATE_MAP.values()),
        }

        # Add template status
        templates = loader.list_available_templates()
        info["templates"] = {
            work_type: {
                "source": source,
                "path": str(loader.get_template_path(work_type)),
            }
            for work_type, source in templates.items()
        }

        return info

    except Exception as e:
        return {"error": str(e)}


def main():
    parser = argparse.ArgumentParser(
        description="Rally Template CLI - Manage Rally work item templates"
    )

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # Copy templates command
    copy_parser = subparsers.add_parser(
        "copy", help="Copy default templates to .claude/.fe-sm-templates/"
    )
    copy_parser.add_argument(
        "--reset",
        action="store_true",
        help="Reset existing templates (overwrite)",
    )
    copy_parser.add_argument(
        "--type",
        help="Copy only specific template type (epic, story, task, etc.)",
    )

    # List templates command
    list_parser = subparsers.add_parser(
        "list", help="List available templates and sources"
    )

    # Render template command
    render_parser = subparsers.add_parser(
        "render", help="Render a template with variables"
    )
    render_parser.add_argument(
        "--type",
        required=True,
        help="Template type (epic, story, task, etc.)",
    )
    render_parser.add_argument(
        "--vars",
        help='Variables as JSON (e.g., \'{"role":"developer","want":"feature"}\')',
    )

    # Info command
    info_parser = subparsers.add_parser("info", help="Show template system information")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    # Execute command
    try:
        if args.command == "copy":
            result = copy_templates(args)
        elif args.command == "list":
            result = list_templates(args)
        elif args.command == "render":
            result = render_template(args)
        elif args.command == "info":
            result = show_info(args)
        else:
            result = {"error": f"Unknown command: {args.command}"}

        # Output result as JSON
        print(json.dumps(result, indent=2))

        # Exit with error code if there was an error
        if isinstance(result, dict) and "error" in result:
            sys.exit(1)

    except Exception as e:
        print(json.dumps({"error": f"Unexpected error: {str(e)}"}, indent=2))
        sys.exit(1)


if __name__ == "__main__":
    main()
