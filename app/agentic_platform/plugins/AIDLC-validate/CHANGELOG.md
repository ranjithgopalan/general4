# AIDLC-validate Changelog

## **1.4.2** - Commands to Skills Migration Cleanup (2026-02-16)

### Cleanup

- Removed obsolete commands/ directory (functionality already migrated to skills)
- Updated README.md to reflect skills-only architecture
- Updated documentation to show natural language invocation instead of slash commands
- All requirements validation functionality now available through auto-invoked skills (requirements, rai)

**Related**: US795212 - Commands to Skills Migration (Iterations 2-3)

---

## **1.4.1** - Plugin Manifest Fix (2026-02-06)

### Bug Fixes

- Fixed plugin.json path references to match actual directory structure
- Updated skills to use directory format (./skills/requirements, ./skills/rai)

---

## **1.4.0** - 2026.PI1.Iteration1 (2026-01-22)

**Sprint Period**: January 8 - January 22, 2026

### Plugin Performance Optimizations (F116882: Run Optimizer and Implement Recommendations)

- **US774970**: AIDLC-validate optimization - Enhanced coverage checks through optimized traversal
