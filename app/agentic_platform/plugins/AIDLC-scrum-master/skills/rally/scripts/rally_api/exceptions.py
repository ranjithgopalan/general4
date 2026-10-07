"""Rally API custom exceptions."""

from typing import Optional


class RallyAPIKeyError(Exception):
    """Raised when Rally API key is missing or invalid."""
    pass


class RallyItemNotFoundError(Exception):
    """Raised when a Rally item is not found. Includes workspace context."""

    def __init__(
        self,
        formatted_id: str,
        item_type: str,
        workspaces_searched: list[str],
        default_workspace: Optional[str] = None,
    ):
        self.formatted_id = formatted_id
        self.item_type = item_type
        self.workspaces_searched = workspaces_searched
        self.default_workspace = default_workspace

        message = (
            f"Rally {item_type} '{formatted_id}' not found.\n\n"
            f"Searched {len(workspaces_searched)} workspace(s): {', '.join(workspaces_searched)}\n\n"
            f"Possible causes:\n"
            f"  - The ID may be incorrect or have a typo\n"
            f"  - The item may be in a different workspace you don't have access to\n"
            f"  - The item may have been deleted\n"
        )

        if default_workspace:
            message += f"\nDefault workspace: {default_workspace}\n"

        message += (
            f"\nTo resolve:\n"
            f"  1. Verify the ID is correct\n"
            f"  2. Check if you have access to all required workspaces\n"
            f"  3. Try searching by name instead: api.search_by_name('{item_type}', 'name')"
        )

        super().__init__(message)


class RallyValidationError(Exception):
    """Raised when Rally validation fails during create/update operations."""

    def __init__(self, operation: str, errors: list, object_type: str = None):
        """
        Create a detailed validation error message with actionable next steps.

        Args:
            operation: The operation that failed (e.g., "create", "update")
            errors: List of error messages from Rally API
            object_type: The type of object being created/updated (e.g., "Capability", "Feature")
        """
        self.operation = operation
        self.errors = errors
        self.object_type = object_type

        # Build detailed error message
        message_parts = [f"\nRally {operation} operation failed with validation errors:\n"]

        for error in errors:
            message_parts.append(f"  • {error}")

            # Provide specific guidance for common validation errors
            guidance = self._get_error_guidance(error, object_type)
            if guidance:
                message_parts.append(f"\n{guidance}\n")

        super().__init__("\n".join(message_parts))

    def _get_error_guidance(self, error: str, object_type: str = None) -> str:
        """
        Provide specific guidance for common Rally validation errors.

        Args:
            error: The error message from Rally
            object_type: The type of object (if known)

        Returns:
            Formatted guidance string with valid values and next steps
        """
        error_lower = error.lower()

        # ScheduleState field validation (for User Stories) - Check BEFORE State
        if "schedulestate" in error_lower and ("null" in error_lower or "should not be" in error_lower):
            return (
                "    → The 'ScheduleState' field is required for User Stories.\n"
                "    → Valid schedule states:\n"
                "       • 'Defined' - Story is defined but not started\n"
                "       • 'In-Progress' - Story is actively being worked on\n"
                "       • 'Completed' - Story development is complete\n"
                "       • 'Accepted' - Story has been accepted by Product Owner\n"
                "    → Next steps: Add schedule_state='Defined' to your create_story call"
            )

        # State field validation (for Portfolio Items and Tasks)
        if "state should not be null" in error_lower or "state" in error_lower and "null" in error_lower:
            if object_type and object_type.lower() in ["capability", "epic", "initiative", "theme", "feature"]:
                return (
                    "    → The 'State' field is required for portfolio items.\n"
                    "    → Valid states: 'Funnel', 'Backlog', 'Analyzing', 'Approved', 'Done'\n"
                    "    → Next steps:\n"
                    "       1. For features: Add state='Funnel' to your create call\n"
                    "       2. For other portfolio items: Add state='Backlog' to your create call\n"
                    "       3. Check if you're inheriting state from a parent correctly"
                )
            else:
                return (
                    "    → The 'State' field is required.\n"
                    "    → For features: 'Funnel', 'Backlog', 'Analyzing', 'Approved', 'Done'\n"
                    "    → For other portfolio items: 'Backlog', 'Analyzing', 'Approved', 'Done'\n"
                    "    → For tasks: 'Defined', 'In-Progress', 'Completed'\n"
                    "    → Next steps: Add the appropriate state field to your create/update call"
                )

        # Name field validation
        if "name" in error_lower and ("null" in error_lower or "required" in error_lower):
            return (
                "    → The 'Name' field is required for all Rally objects.\n"
                "    → Next steps: Add a name parameter to your create call"
            )

        # Parent reference validation
        if "parent" in error_lower and ("null" in error_lower or "required" in error_lower or "invalid" in error_lower):
            return (
                "    → The 'Parent' reference is invalid or required.\n"
                "    → Next steps:\n"
                "       1. Ensure parent_ref is a valid Rally _ref (e.g., '/portfolioitem/feature/12345')\n"
                "       2. Verify the parent object exists in Rally\n"
                "       3. Check that you have permission to link to the parent"
            )

        # Owner reference validation
        if "owner" in error_lower and ("invalid" in error_lower or "not found" in error_lower):
            return (
                "    → The 'Owner' reference is invalid.\n"
                "    → Next steps:\n"
                "       1. Ensure owner_ref is a valid Rally user _ref (e.g., '/user/12345')\n"
                "       2. Use api.search_users(name='username') to find valid user references\n"
                "       3. Verify the user is active and has access to this workspace"
            )

        # Permission errors
        if "permission" in error_lower or "not allowed" in error_lower or "access denied" in error_lower:
            return (
                "    → You don't have permission to perform this operation.\n"
                "    → Next steps:\n"
                "       1. Verify you have the correct workspace permissions\n"
                "       2. Check if the object is locked or in a restricted state\n"
                "       3. Contact your Rally workspace administrator for access"
            )

        # Duplicate name errors
        if "duplicate" in error_lower or "already exists" in error_lower:
            return (
                "    → An object with this name already exists.\n"
                "    → Next steps:\n"
                "       1. Use a different, unique name\n"
                "       2. Search for existing objects: api.search_by_name(type, name)\n"
                "       3. Consider updating the existing object instead of creating a new one"
            )

        # No specific guidance available
        return (
            "    → Next steps:\n"
            "       1. Review the error message above for specific field requirements\n"
            "       2. Check Rally API documentation for field constraints\n"
            "       3. Verify all required fields are provided with valid values"
        )
