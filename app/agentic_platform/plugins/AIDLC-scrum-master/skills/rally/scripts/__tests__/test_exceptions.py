"""
Unit tests for rally_api.exceptions module.

Tests custom Rally exceptions including:
- RallyAPIKeyError
- RallyItemNotFoundError with workspace context
- RallyValidationError with detailed guidance
- Error message formatting and actionability
"""

import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from rally_api.exceptions import (
    RallyAPIKeyError,
    RallyItemNotFoundError,
    RallyValidationError
)


class TestRallyAPIKeyError:
    """Tests for RallyAPIKeyError exception."""

    def test_raise_api_key_error(self):
        """Test raising RallyAPIKeyError."""
        with pytest.raises(RallyAPIKeyError):
            raise RallyAPIKeyError("API key is missing")

    def test_api_key_error_message(self):
        """Test API key error contains message."""
        with pytest.raises(RallyAPIKeyError) as exc_info:
            raise RallyAPIKeyError("API key configuration not found")

        assert "API key" in str(exc_info.value)


class TestRallyItemNotFoundError:
    """Tests for RallyItemNotFoundError exception."""

    def test_raise_item_not_found_basic(self):
        """Test raising basic item not found error."""
        with pytest.raises(RallyItemNotFoundError):
            raise RallyItemNotFoundError(
                formatted_id="US12345",
                item_type="User Story",
                workspaces_searched=["General Insurance Workspace"]
            )

    def test_item_not_found_includes_formatted_id(self):
        """Test error message includes formatted ID."""
        with pytest.raises(RallyItemNotFoundError) as exc_info:
            raise RallyItemNotFoundError(
                formatted_id="F67890",
                item_type="Feature",
                workspaces_searched=["Workspace A"]
            )

        assert "F67890" in str(exc_info.value)

    def test_item_not_found_includes_item_type(self):
        """Test error message includes item type."""
        with pytest.raises(RallyItemNotFoundError) as exc_info:
            raise RallyItemNotFoundError(
                formatted_id="US12345",
                item_type="User Story",
                workspaces_searched=["Workspace A"]
            )

        assert "User Story" in str(exc_info.value)

    def test_item_not_found_lists_workspaces(self):
        """Test error message lists all searched workspaces."""
        workspaces = ["Workspace A", "Workspace B", "Workspace C"]

        with pytest.raises(RallyItemNotFoundError) as exc_info:
            raise RallyItemNotFoundError(
                formatted_id="US12345",
                item_type="User Story",
                workspaces_searched=workspaces
            )

        error_message = str(exc_info.value)
        assert "Workspace A" in error_message
        assert "Workspace B" in error_message
        assert "Workspace C" in error_message
        assert "3" in error_message  # Number of workspaces

    def test_item_not_found_includes_default_workspace(self):
        """Test error message shows default workspace when provided."""
        with pytest.raises(RallyItemNotFoundError) as exc_info:
            raise RallyItemNotFoundError(
                formatted_id="US12345",
                item_type="User Story",
                workspaces_searched=["Workspace A"],
                default_workspace="Workspace A"
            )

        assert "Default workspace" in str(exc_info.value)
        assert "Workspace A" in str(exc_info.value)

    def test_item_not_found_provides_resolution_steps(self):
        """Test error message includes resolution steps."""
        with pytest.raises(RallyItemNotFoundError) as exc_info:
            raise RallyItemNotFoundError(
                formatted_id="US12345",
                item_type="User Story",
                workspaces_searched=["Workspace A"]
            )

        error_message = str(exc_info.value)
        assert "To resolve:" in error_message
        assert "Verify the ID" in error_message
        assert "search_by_name" in error_message

    def test_item_not_found_attributes(self):
        """Test exception stores attributes correctly."""
        error = RallyItemNotFoundError(
            formatted_id="US12345",
            item_type="User Story",
            workspaces_searched=["WS1", "WS2"],
            default_workspace="WS1"
        )

        assert error.formatted_id == "US12345"
        assert error.item_type == "User Story"
        assert error.workspaces_searched == ["WS1", "WS2"]
        assert error.default_workspace == "WS1"


class TestRallyValidationError:
    """Tests for RallyValidationError exception."""

    def test_raise_validation_error_basic(self):
        """Test raising basic validation error."""
        with pytest.raises(RallyValidationError):
            raise RallyValidationError(
                operation="create",
                errors=["Name is required"],
                object_type="Feature"
            )

    def test_validation_error_includes_operation(self):
        """Test error message includes operation type."""
        with pytest.raises(RallyValidationError) as exc_info:
            raise RallyValidationError(
                operation="update",
                errors=["Invalid field"],
                object_type="Story"
            )

        assert "update" in str(exc_info.value)

    def test_validation_error_includes_all_errors(self):
        """Test error message includes all validation errors."""
        errors = [
            "Name is required",
            "State should not be null",
            "Owner reference is invalid"
        ]

        with pytest.raises(RallyValidationError) as exc_info:
            raise RallyValidationError(
                operation="create",
                errors=errors,
                object_type="Feature"
            )

        error_message = str(exc_info.value)
        for error in errors:
            assert error in error_message

    def test_validation_error_state_guidance_for_feature(self):
        """Test validation error provides state guidance for features."""
        with pytest.raises(RallyValidationError) as exc_info:
            raise RallyValidationError(
                operation="create",
                errors=["State should not be null"],
                object_type="Feature"
            )

        error_message = str(exc_info.value)
        assert "State" in error_message
        assert "Funnel" in error_message
        assert "Backlog" in error_message

    def test_validation_error_state_guidance_for_capability(self):
        """Test validation error provides state guidance for capabilities."""
        with pytest.raises(RallyValidationError) as exc_info:
            raise RallyValidationError(
                operation="create",
                errors=["state should not be null"],
                object_type="Capability"
            )

        error_message = str(exc_info.value)
        assert "State" in error_message
        assert "required" in error_message

    def test_validation_error_schedulestate_guidance(self):
        """Test validation error provides ScheduleState guidance for stories."""
        with pytest.raises(RallyValidationError) as exc_info:
            raise RallyValidationError(
                operation="create",
                errors=["ScheduleState should not be null"],
                object_type="User Story"
            )

        error_message = str(exc_info.value)
        assert "ScheduleState" in error_message
        assert "Defined" in error_message
        assert "In-Progress" in error_message
        assert "Completed" in error_message
        assert "Accepted" in error_message

    def test_validation_error_name_guidance(self):
        """Test validation error provides Name field guidance."""
        with pytest.raises(RallyValidationError) as exc_info:
            raise RallyValidationError(
                operation="create",
                errors=["Name is null"],
                object_type="Feature"
            )

        error_message = str(exc_info.value)
        assert "Name" in error_message
        assert "required" in error_message

    def test_validation_error_parent_guidance(self):
        """Test validation error provides Parent reference guidance."""
        with pytest.raises(RallyValidationError) as exc_info:
            raise RallyValidationError(
                operation="create",
                errors=["Parent reference is invalid"],
                object_type="Feature"
            )

        error_message = str(exc_info.value)
        assert "Parent" in error_message
        assert "_ref" in error_message

    def test_validation_error_owner_guidance(self):
        """Test validation error provides Owner reference guidance."""
        with pytest.raises(RallyValidationError) as exc_info:
            raise RallyValidationError(
                operation="update",
                errors=["Owner not found"],
                object_type="Story"
            )

        error_message = str(exc_info.value)
        assert "Owner" in error_message
        assert "user" in error_message or "search_users" in error_message

    def test_validation_error_permission_guidance(self):
        """Test validation error provides permission guidance."""
        with pytest.raises(RallyValidationError) as exc_info:
            raise RallyValidationError(
                operation="update",
                errors=["You do not have permission to perform this operation"],
                object_type="Feature"
            )

        error_message = str(exc_info.value)
        assert "permission" in error_message
        assert "access" in error_message or "administrator" in error_message

    def test_validation_error_duplicate_guidance(self):
        """Test validation error provides duplicate object guidance."""
        with pytest.raises(RallyValidationError) as exc_info:
            raise RallyValidationError(
                operation="create",
                errors=["An object with this name already exists"],
                object_type="Feature"
            )

        error_message = str(exc_info.value)
        assert "duplicate" in error_message or "already exists" in error_message
        assert "unique" in error_message or "different" in error_message

    def test_validation_error_generic_guidance(self):
        """Test validation error provides generic guidance for unknown errors."""
        with pytest.raises(RallyValidationError) as exc_info:
            raise RallyValidationError(
                operation="create",
                errors=["Some unknown validation error"],
                object_type="Feature"
            )

        error_message = str(exc_info.value)
        assert "Next steps" in error_message
        assert "documentation" in error_message or "required fields" in error_message

    def test_validation_error_attributes(self):
        """Test exception stores attributes correctly."""
        errors = ["Error 1", "Error 2"]
        error = RallyValidationError(
            operation="create",
            errors=errors,
            object_type="Feature"
        )

        assert error.operation == "create"
        assert error.errors == errors
        assert error.object_type == "Feature"

    def test_validation_error_multiple_errors_with_guidance(self):
        """Test validation error with multiple errors shows all guidance."""
        errors = [
            "State should not be null",
            "Name is required",
            "Owner reference is invalid"
        ]

        with pytest.raises(RallyValidationError) as exc_info:
            raise RallyValidationError(
                operation="create",
                errors=errors,
                object_type="Feature"
            )

        error_message = str(exc_info.value)
        # Should contain guidance for all errors
        assert "State" in error_message
        assert "Name" in error_message
        assert "Owner" in error_message

    def test_validation_error_case_insensitive_matching(self):
        """Test error guidance matching is case insensitive."""
        with pytest.raises(RallyValidationError) as exc_info:
            raise RallyValidationError(
                operation="create",
                errors=["NAME IS REQUIRED"],  # Uppercase
                object_type="Feature"
            )

        error_message = str(exc_info.value)
        assert "Name" in error_message or "required" in error_message


class TestExceptionInheritance:
    """Tests for exception inheritance and behavior."""

    def test_rally_api_key_error_is_exception(self):
        """Test RallyAPIKeyError inherits from Exception."""
        assert issubclass(RallyAPIKeyError, Exception)

    def test_rally_item_not_found_error_is_exception(self):
        """Test RallyItemNotFoundError inherits from Exception."""
        assert issubclass(RallyItemNotFoundError, Exception)

    def test_rally_validation_error_is_exception(self):
        """Test RallyValidationError inherits from Exception."""
        assert issubclass(RallyValidationError, Exception)

    def test_exceptions_can_be_caught_as_exception(self):
        """Test custom exceptions can be caught as generic Exception."""
        with pytest.raises(Exception):
            raise RallyAPIKeyError("Test error")

        with pytest.raises(Exception):
            raise RallyItemNotFoundError("US123", "Story", ["WS1"])

        with pytest.raises(Exception):
            raise RallyValidationError("create", ["Error"], "Feature")
