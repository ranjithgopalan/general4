#!/usr/bin/env python3
"""
Rally Backup Utility

Creates backups of Rally work items before making changes.
Backups are stored in .claude/.fe-sm/backups/ with timestamps.
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Any

from .paths import PluginPaths


class RallyBackup:
    """Backup utility for Rally work items."""

    def __init__(self, backup_dir: Optional[Path] = None):
        """
        Initialize backup utility.

        Args:
            backup_dir: Custom backup directory (defaults to .claude/.fe-sm/backups/)
        """
        if backup_dir:
            self.backup_dir = backup_dir
        else:
            self.backup_dir = PluginPaths.get_backup_dir()

    def create_backup(
        self,
        operation: str,
        items: List[Dict[str, Any]],
        context: Optional[Dict[str, Any]] = None,
    ) -> Path:
        """
        Create a backup of Rally work items.

        Args:
            operation: Type of operation (CREATE, UPDATE, DELETE, BULK_ASSIGN, etc.)
            items: List of Rally items to backup
            context: Additional context (feature, epic, iteration, etc.)

        Returns:
            Path to backup file
        """
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

        # Generate backup filename
        if context and context.get("feature_id"):
            filename = f"{timestamp}_{operation}_{context['feature_id']}.json"
        elif context and context.get("iteration"):
            iter_name = context["iteration"].replace(".", "_")
            filename = f"{timestamp}_{operation}_{iter_name}.json"
        else:
            filename = f"{timestamp}_{operation}.json"

        backup_path = self.backup_dir / filename

        # Prepare backup data
        backup_data = {
            "timestamp": timestamp,
            "operation": operation,
            "context": context or {},
            "items": items,
            "backup_version": "1.0",
        }

        # Write backup file
        with open(backup_path, "w", encoding="utf-8") as f:
            json.dump(backup_data, f, indent=2)

        return backup_path

    def backup_before_update(
        self, rally_api, items: List[str], operation: str = "UPDATE",
        context: Optional[Dict[str, Any]] = None
    ) -> Path:
        """
        Backup items before updating them.

        Args:
            rally_api: RallyAPI instance
            items: List of Rally item IDs or refs to backup
            operation: Type of operation (UPDATE, DELETE, etc.)
            context: Optional context information (feature, epic, iteration, etc.)

        Returns:
            Path to backup file
        """
        backed_up_items = []

        for item_id in items:
            # Find and fetch the item
            item = rally_api.find_by_formatted_id(item_id)
            if item:
                # Fetch full details
                full_item = rally_api.get_object(
                    item["_ref"],
                    fetch="FormattedID,Name,Description,Owner,PlanEstimate,Estimate,ScheduleState,State,Tasks,WorkProduct,Release,Iteration,PortfolioItem",
                )
                backed_up_items.append(full_item)

        return self.create_backup(operation, backed_up_items, context)

    def backup_feature_tree(self, rally_api, feature_id: str) -> Path:
        """
        Backup an entire feature with all stories and tasks.

        Args:
            rally_api: RallyAPI instance
            feature_id: Feature ID (e.g., F12345)

        Returns:
            Path to backup file
        """
        # Get feature
        feature = rally_api.find_portfolio_item(feature_id, "feature")
        if not feature:
            raise ValueError(f"Feature {feature_id} not found")

        # Get full feature details
        feature_full = rally_api.get_object(
            feature["_ref"], fetch="FormattedID,Name,Description,Release,Iteration,State"
        )

        # Get all stories
        stories = rally_api.query(
            "hierarchicalrequirement",
            query=f'(PortfolioItem = "{feature["_ref"]}")',
            fetch="FormattedID,Name,Description,Owner,PlanEstimate,ScheduleState,Release,Iteration,_ref",
            include_project_scope=False,
        )

        # Get all tasks for each story
        all_items = [feature_full]
        for story in stories:
            # Get full story details
            story_full = rally_api.get_object(story["_ref"])
            all_items.append(story_full)

            # Get tasks for this story
            tasks = rally_api.query(
                "task",
                query=f'(WorkProduct = "{story["_ref"]}")',
                fetch="FormattedID,Name,Description,Owner,Estimate,State,_ref",
            )
            all_items.extend(tasks)

        context = {
            "feature_id": feature_id,
            "feature_name": feature.get("Name", ""),
            "story_count": len(stories),
            "total_items": len(all_items),
        }

        return self.create_backup("FEATURE_TREE_BACKUP", all_items, context)

    def list_backups(self, limit: int = 20) -> List[Dict[str, Any]]:
        """
        List recent backups.

        Args:
            limit: Maximum number of backups to return

        Returns:
            List of backup metadata
        """
        backups = []

        for backup_file in sorted(self.backup_dir.glob("*.json"), reverse=True)[
            :limit
        ]:
            try:
                with open(backup_file, "r", encoding="utf-8") as f:
                    data = json.load(f)

                backups.append(
                    {
                        "filename": backup_file.name,
                        "path": str(backup_file),
                        "timestamp": data.get("timestamp"),
                        "operation": data.get("operation"),
                        "context": data.get("context", {}),
                        "item_count": len(data.get("items", [])),
                    }
                )
            except Exception as e:
                print(f"Error reading backup {backup_file}: {e}")

        return backups

    def load_backup(self, backup_path: Path) -> Dict[str, Any]:
        """
        Load a backup file.

        Args:
            backup_path: Path to backup file

        Returns:
            Backup data
        """
        with open(backup_path, "r", encoding="utf-8") as f:
            return json.load(f)

    def restore_from_backup(self, rally_api, backup_path: Path, dry_run: bool = True):
        """
        Restore Rally items from a backup.

        Args:
            rally_api: RallyAPI instance
            backup_path: Path to backup file
            dry_run: If True, show what would be restored without actually doing it

        Returns:
            Dict with restore results
        """
        backup_data = self.load_backup(backup_path)

        results = {
            "dry_run": dry_run,
            "timestamp": backup_data["timestamp"],
            "operation": backup_data["operation"],
            "items_to_restore": [],
            "restored": [],
            "errors": [],
        }

        for item in backup_data["items"]:
            item_id = item.get("FormattedID", "Unknown")
            item_ref = item.get("_ref")

            results["items_to_restore"].append(
                {"id": item_id, "name": item.get("Name", ""), "ref": item_ref}
            )

            if not dry_run and item_ref:
                try:
                    # Restore item by updating with backed-up values
                    # Remove read-only fields
                    update_fields = {
                        k: v
                        for k, v in item.items()
                        if k
                        not in [
                            "_ref",
                            "_refObjectUUID",
                            "_type",
                            "FormattedID",
                            "ObjectID",
                            "CreationDate",
                            "_CreatedAt",
                        ]
                    }

                    rally_api.update_object(item_ref, update_fields)
                    results["restored"].append(item_id)
                except Exception as e:
                    results["errors"].append({"id": item_id, "error": str(e)})

        return results


# CLI interface
def main():
    """CLI interface for backup utility."""
    import argparse
    import sys

    sys.path.insert(0, str(Path(__file__).parent))
    from rally_api.client import RallyAPI

    parser = argparse.ArgumentParser(description="Rally Backup Utility")
    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # Backup feature command
    backup_parser = subparsers.add_parser(
        "backup-feature", help="Backup a feature with all stories and tasks"
    )
    backup_parser.add_argument("feature_id", help="Feature ID (e.g., F12345)")

    # List backups command
    list_parser = subparsers.add_parser("list", help="List recent backups")
    list_parser.add_argument(
        "--limit", type=int, default=20, help="Number of backups to show"
    )

    # Restore command
    restore_parser = subparsers.add_parser("restore", help="Restore from backup")
    restore_parser.add_argument("backup_file", help="Path to backup file")
    restore_parser.add_argument(
        "--dry-run",
        action="store_true",
        default=True,
        help="Show what would be restored without actually doing it",
    )
    restore_parser.add_argument(
        "--execute", action="store_true", help="Actually execute the restore"
    )

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    backup_util = RallyBackup()

    if args.command == "backup-feature":
        api = RallyAPI()
        print(f"Backing up feature {args.feature_id}...")
        backup_path = backup_util.backup_feature_tree(api, args.feature_id)
        print(f"[OK] Backup created: {backup_path}")

    elif args.command == "list":
        backups = backup_util.list_backups(limit=args.limit)
        print(f"\nRecent Backups ({len(backups)}):\n")
        for backup in backups:
            print(f"  • {backup['filename']}")
            print(f"    Operation: {backup['operation']}")
            print(f"    Items: {backup['item_count']}")
            if backup["context"]:
                print(f"    Context: {backup['context']}")
            print()

    elif args.command == "restore":
        api = RallyAPI()
        dry_run = not args.execute
        backup_path = Path(args.backup_file)

        print(f"Restoring from: {backup_path}")
        results = backup_util.restore_from_backup(api, backup_path, dry_run=dry_run)

        if dry_run:
            print("\n[DRY RUN] No changes will be made\n")

        print(f"Items to restore: {len(results['items_to_restore'])}")
        for item in results["items_to_restore"]:
            print(f"  • {item['id']}: {item['name']}")

        if not dry_run:
            print(f"\n✅ Restored: {len(results['restored'])} items")
            if results["errors"]:
                print(f"❌ Errors: {len(results['errors'])}")
                for error in results["errors"]:
                    print(f"  • {error['id']}: {error['error']}")


if __name__ == "__main__":
    main()
