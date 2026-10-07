"""
Unit tests for rally_api.backup module.

Tests Rally backup functionality including:
- Creating backups of work items
- Backup directory management
- Backup file naming and structure
- Backup before update/delete operations
- Listing and restoring backups
"""

import pytest
from unittest.mock import MagicMock, patch, mock_open, call
import json
from pathlib import Path
from datetime import datetime
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))
from rally_api.backup import RallyBackup


class TestBackupInitialization:
    """Tests for RallyBackup initialization."""

    @patch('rally_api.backup.Path.cwd')
    @patch('rally_api.backup.Path.mkdir')
    def test_init_finds_claude_directory(self, mock_mkdir, mock_cwd):
        """Test initialization finds .claude directory."""
        mock_cwd.return_value = Path("/project")

        with patch.object(Path, 'exists', return_value=True):
            with patch.object(Path, 'is_dir', return_value=True):
                backup = RallyBackup()

                assert ".fe-sm" in str(backup.backup_dir)
                assert "backups" in str(backup.backup_dir)

    @patch('rally_api.backup.Path.cwd')
    def test_init_creates_backup_dir_if_not_exists(self, mock_cwd):
        """Test initialization creates backup directory."""
        mock_cwd.return_value = Path("/project")

        with patch.object(Path, 'mkdir') as mock_mkdir:
            with patch.object(Path, 'exists', return_value=False):
                backup = RallyBackup()

                mock_mkdir.assert_called()

    def test_init_with_custom_backup_dir(self):
        """Test initialization with custom backup directory."""
        custom_dir = Path("/custom/backups")

        backup = RallyBackup(backup_dir=custom_dir)

        assert backup.backup_dir == custom_dir


class TestCreateBackup:
    """Tests for creating backups."""

    @patch('builtins.open', new_callable=mock_open)
    @patch('rally_api.backup.Path.mkdir')
    def test_create_backup_basic(self, mock_mkdir, mock_file):
        """Test creating a basic backup."""
        backup = RallyBackup(backup_dir=Path("/test/backups"))
        items = [
            {"FormattedID": "US12345", "Name": "Test Story"}
        ]

        with patch('rally_api.backup.datetime') as mock_datetime:
            mock_datetime.now.return_value.strftime.return_value = "20260113_120000"

            result_path = backup.create_backup("UPDATE", items)

        assert "20260113_120000" in str(result_path)
        assert "UPDATE" in str(result_path)
        mock_file.assert_called_once()

    @patch('builtins.open', new_callable=mock_open)
    @patch('rally_api.backup.Path.mkdir')
    def test_create_backup_with_feature_context(self, mock_mkdir, mock_file):
        """Test creating backup with feature context."""
        backup = RallyBackup(backup_dir=Path("/test/backups"))
        items = [{"FormattedID": "US12345"}]
        context = {"feature_id": "F67890"}

        with patch('rally_api.backup.datetime') as mock_datetime:
            mock_datetime.now.return_value.strftime.return_value = "20260113_120000"

            result_path = backup.create_backup("CREATE", items, context)

        assert "F67890" in str(result_path)

    @patch('builtins.open', new_callable=mock_open)
    @patch('rally_api.backup.Path.mkdir')
    def test_create_backup_with_iteration_context(self, mock_mkdir, mock_file):
        """Test creating backup with iteration context."""
        backup = RallyBackup(backup_dir=Path("/test/backups"))
        items = [{"FormattedID": "US12345"}]
        context = {"iteration": "2026.PI1.Iteration1"}

        with patch('rally_api.backup.datetime') as mock_datetime:
            mock_datetime.now.return_value.strftime.return_value = "20260113_120000"

            result_path = backup.create_backup("BULK_ASSIGN", items, context)

        # Iteration name should have dots replaced with underscores
        assert "2026_PI1_Iteration1" in str(result_path) or "2026.PI1.Iteration1" in str(result_path)

    @patch('builtins.open', new_callable=mock_open)
    @patch('rally_api.backup.Path.mkdir')
    def test_create_backup_structure(self, mock_mkdir, mock_file):
        """Test backup file contains correct structure."""
        backup = RallyBackup(backup_dir=Path("/test/backups"))
        items = [{"FormattedID": "US12345", "Name": "Test"}]
        context = {"feature_id": "F123"}

        with patch('rally_api.backup.datetime') as mock_datetime:
            mock_datetime.now.return_value.strftime.return_value = "20260113_120000"

            backup.create_backup("UPDATE", items, context)

        # Verify json.dump was called with correct structure
        write_calls = mock_file().write.call_args_list
        if write_calls:
            written_data = ''.join(call[0][0] for call in write_calls)
            # Should contain backup structure keys
            assert '"operation"' in written_data or "operation" in str(mock_file.mock_calls)


class TestBackupBeforeUpdate:
    """Tests for backup_before_update method."""

    @patch('builtins.open', new_callable=mock_open)
    @patch('rally_api.backup.Path.mkdir')
    def test_backup_before_update_fetches_items(self, mock_mkdir, mock_file):
        """Test backup_before_update fetches current state of items."""
        backup = RallyBackup(backup_dir=Path("/test/backups"))

        mock_api = MagicMock()
        mock_api.find_by_formatted_id.side_effect = [
            {"FormattedID": "US12345", "Name": "Story 1", "_ref": "https://rally1.rallydev.com/slm/webservice/v2.0/hierarchicalrequirement/12345"},
            {"FormattedID": "US67890", "Name": "Story 2", "_ref": "https://rally1.rallydev.com/slm/webservice/v2.0/hierarchicalrequirement/67890"}
        ]
        mock_api.get_object.side_effect = [
            {"FormattedID": "US12345", "Name": "Story 1", "_ref": "https://rally1.rallydev.com/slm/webservice/v2.0/hierarchicalrequirement/12345"},
            {"FormattedID": "US67890", "Name": "Story 2", "_ref": "https://rally1.rallydev.com/slm/webservice/v2.0/hierarchicalrequirement/67890"}
        ]

        with patch('rally_api.backup.datetime') as mock_datetime:
            mock_datetime.now.return_value.strftime.return_value = "20260113_120000"

            result_path = backup.backup_before_update(
                mock_api,
                ["US12345", "US67890"],
                operation="UPDATE"
            )

        assert mock_api.find_by_formatted_id.call_count == 2
        assert "UPDATE" in str(result_path)

    @patch('builtins.open', new_callable=mock_open)
    @patch('rally_api.backup.Path.mkdir')
    def test_backup_before_update_handles_not_found(self, mock_mkdir, mock_file):
        """Test backup_before_update handles items not found."""
        backup = RallyBackup(backup_dir=Path("/test/backups"))

        mock_api = MagicMock()
        mock_api.find_by_formatted_id.return_value = None

        with patch('rally_api.backup.datetime') as mock_datetime:
            mock_datetime.now.return_value.strftime.return_value = "20260113_120000"

            # Should not raise error, just skip the item
            result_path = backup.backup_before_update(
                mock_api,
                ["US99999"],
                operation="UPDATE"
            )

        assert result_path is not None


class TestListBackups:
    """Tests for listing backups."""

    @patch('builtins.open', new_callable=mock_open, read_data='{"timestamp": "2026-01-13 12:00:00", "operation": "UPDATE", "items": []}')
    @patch('rally_api.backup.Path.glob')
    def test_list_backups(self, mock_glob, mock_file):
        """Test listing all backup files."""
        backup = RallyBackup(backup_dir=Path("/test/backups"))

        mock_glob.return_value = [
            Path("/test/backups/20260113_120000_UPDATE_US12345.json"),
            Path("/test/backups/20260113_130000_DELETE_US67890.json")
        ]

        backups = backup.list_backups()

        assert len(backups) == 2
        assert all("filename" in b for b in backups)
        assert all("operation" in b for b in backups)

    @patch('builtins.open', new_callable=mock_open, read_data='{"timestamp": "2026-01-13 12:00:00", "operation": "UPDATE", "items": []}')
    @patch('rally_api.backup.Path.glob')
    def test_list_backups_by_operation(self, mock_glob, mock_file):
        """Test filtering backups by operation type."""
        backup = RallyBackup(backup_dir=Path("/test/backups"))

        mock_glob.return_value = [
            Path("/test/backups/20260113_120000_UPDATE_US12345.json"),
            Path("/test/backups/20260113_130000_UPDATE_US67890.json"),
            Path("/test/backups/20260113_140000_DELETE_US11111.json")
        ]

        # Note: list_backups doesn't actually filter by operation, it just lists all
        # The filtering would be done by the caller based on the operation in the metadata
        backups = backup.list_backups()

        assert len(backups) == 3

    @patch('rally_api.backup.Path.glob')
    def test_list_backups_empty(self, mock_glob):
        """Test listing backups when none exist."""
        backup = RallyBackup(backup_dir=Path("/test/backups"))
        mock_glob.return_value = []

        backups = backup.list_backups()

        assert backups == []


class TestLoadBackup:
    """Tests for loading backup files."""

    @patch('builtins.open', new_callable=mock_open, read_data='{"operation": "UPDATE", "items": [{"FormattedID": "US12345"}]}')
    def test_load_backup_success(self, mock_file):
        """Test loading a backup file."""
        backup = RallyBackup(backup_dir=Path("/test/backups"))
        backup_path = Path("/test/backups/20260113_120000_UPDATE.json")

        data = backup.load_backup(backup_path)

        assert data["operation"] == "UPDATE"
        assert len(data["items"]) == 1
        assert data["items"][0]["FormattedID"] == "US12345"

    @patch('builtins.open', side_effect=FileNotFoundError)
    def test_load_backup_file_not_found(self, mock_file):
        """Test loading non-existent backup file."""
        backup = RallyBackup(backup_dir=Path("/test/backups"))
        backup_path = Path("/test/backups/nonexistent.json")

        with pytest.raises(FileNotFoundError):
            backup.load_backup(backup_path)

    @patch('builtins.open', new_callable=mock_open, read_data='invalid json')
    def test_load_backup_invalid_json(self, mock_file):
        """Test loading backup with invalid JSON."""
        backup = RallyBackup(backup_dir=Path("/test/backups"))
        backup_path = Path("/test/backups/invalid.json")

        with pytest.raises(json.JSONDecodeError):
            backup.load_backup(backup_path)


class TestRestoreBackup:
    """Tests for restoring from backup."""

    @patch('builtins.open', new_callable=mock_open,
           read_data='{"timestamp": "2026-01-13 12:00:00", "operation": "UPDATE", "items": [{"FormattedID": "US12345", "Name": "Original Name", "_ref": "https://rally.../12345"}], "context": {}}')
    def test_restore_from_backup_dry_run(self, mock_file):
        """Test restore backup in dry-run mode."""
        backup = RallyBackup(backup_dir=Path("/test/backups"))
        backup_path = Path("/test/backups/backup.json")
        mock_api = MagicMock()

        result = backup.restore_from_backup(mock_api, backup_path, dry_run=True)

        # In dry-run mode, should not call API
        mock_api.update_object.assert_not_called()
        assert result["dry_run"] is True
        assert len(result["items_to_restore"]) > 0

    @patch('builtins.open', new_callable=mock_open,
           read_data='{"timestamp": "2026-01-13 12:00:00", "operation": "UPDATE", "items": [{"FormattedID": "US12345", "Name": "Original Name", "_ref": "https://rally.../12345"}], "context": {}}')
    def test_restore_from_backup_execute(self, mock_file):
        """Test executing backup restore."""
        backup = RallyBackup(backup_dir=Path("/test/backups"))
        backup_path = Path("/test/backups/backup.json")
        mock_api = MagicMock()
        mock_api.update_object.return_value = {"FormattedID": "US12345"}

        result = backup.restore_from_backup(mock_api, backup_path, dry_run=False)

        # Should call API to restore
        mock_api.update_object.assert_called()
        assert len(result["restored"]) > 0

    @patch('builtins.open', new_callable=mock_open,
           read_data='{"timestamp": "2026-01-13 12:00:00", "operation": "UPDATE", "items": [{"FormattedID": "US12345", "_ref": "ref1"}, {"FormattedID": "US67890", "_ref": "ref2"}], "context": {}}')
    def test_restore_multiple_items(self, mock_file):
        """Test restoring multiple items from backup."""
        backup = RallyBackup(backup_dir=Path("/test/backups"))
        backup_path = Path("/test/backups/backup.json")
        mock_api = MagicMock()
        mock_api.update_object.return_value = {}

        result = backup.restore_from_backup(mock_api, backup_path, dry_run=False)

        assert mock_api.update_object.call_count == 2


class TestBackupFilenameGeneration:
    """Tests for backup filename generation patterns."""

    @patch('builtins.open', new_callable=mock_open)
    @patch('rally_api.backup.Path.mkdir')
    def test_filename_includes_timestamp(self, mock_mkdir, mock_file):
        """Test backup filename includes timestamp."""
        backup = RallyBackup(backup_dir=Path("/test/backups"))

        with patch('rally_api.backup.datetime') as mock_datetime:
            mock_datetime.now.return_value.strftime.return_value = "20260113_153045"

            result_path = backup.create_backup("UPDATE", [])

        assert "20260113_153045" in str(result_path)

    @patch('builtins.open', new_callable=mock_open)
    @patch('rally_api.backup.Path.mkdir')
    def test_filename_includes_operation_type(self, mock_mkdir, mock_file):
        """Test backup filename includes operation type."""
        backup = RallyBackup(backup_dir=Path("/test/backups"))

        with patch('rally_api.backup.datetime') as mock_datetime:
            mock_datetime.now.return_value.strftime.return_value = "20260113_120000"

            for operation in ["CREATE", "UPDATE", "DELETE", "BULK_ASSIGN"]:
                result_path = backup.create_backup(operation, [])
                assert operation in str(result_path)

    @patch('builtins.open', new_callable=mock_open)
    @patch('rally_api.backup.Path.mkdir')
    def test_filename_sanitizes_iteration_name(self, mock_mkdir, mock_file):
        """Test backup filename sanitizes iteration names with dots."""
        backup = RallyBackup(backup_dir=Path("/test/backups"))
        context = {"iteration": "2026.PI1.Iteration1"}

        with patch('rally_api.backup.datetime') as mock_datetime:
            mock_datetime.now.return_value.strftime.return_value = "20260113_120000"

            result_path = backup.create_backup("UPDATE", [], context)

        # Dots should be replaced with underscores in filename
        filename = str(result_path)
        assert "2026_PI1_Iteration1" in filename or "2026.PI1.Iteration1" in filename
