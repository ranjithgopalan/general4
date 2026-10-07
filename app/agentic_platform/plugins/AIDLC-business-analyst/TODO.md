# AIDLC-business-analyst Plugin - TODO

## Outstanding Work Items

### US774992: Create comprehensive test suite for BRD generation with diverse file types

**Rally Status:** In-Progress
**FormattedID:** US774992
**Priority:** High

#### Description
Create a comprehensive test suite covering various input file types to ensure the BRD generator works reliably with real-world documents.

#### Acceptance Criteria

- [ ] **Test with diverse file types:**
  - [ ] Word documents with complex formatting (tables, styles, nested structures)
  - [ ] Excel files with specific named tables (range extraction tests)
  - [ ] PDFs - both text-based and scanned (OCR validation)
  - [ ] Mixed folder of various formats (DOCX, XLSX, PDF, MSG, PPTX, MD, TXT)

- [ ] **Test Excel table prompting**
  - [ ] Test specific table reference extraction
  - [ ] Test sheet-specific extraction (`--sheet "SheetName"`)
  - [ ] Test range extraction (`--range A1:D10`, `--range Sheet1!B2:F20`)
  - [ ] Test multiple sheet handling (`--sheets "A,B,C"`)

- [ ] **Test error handling**
  - [ ] Corrupt file handling
  - [ ] Unsupported file format handling
  - [ ] Missing file/folder handling
  - [ ] Empty file handling
  - [ ] Permission denied scenarios

- [ ] **Validate output structure**
  - [ ] Verify all template sections are populated
  - [ ] Validate gap indicators for missing content
  - [ ] Verify formatting preservation
  - [ ] Validate both .md and .docx outputs

#### Current Test Coverage

**Existing tests in `__tests__/`:**
- ✅ `brd_command_document_ingestion.test` - Basic document ingestion
- ✅ `brd_generation_integration.test` - End-to-end BRD generation
- ✅ `format_conversion_unit.test` - Format conversion testing
- ✅ `excel_table_prompts.test` - Excel table extraction
- ✅ `error_handling.test` - Error handling scenarios
- ✅ `ambiguity_detection_integrated.test` - Ambiguity detection

**Test input files in `__tests__/.inputs/`:**
- ✅ `error_handling_tests/` - Error test inputs
- ✅ `excel_table_tests/` - Excel table test files
- ✅ `format_conversion_tests/` - Format conversion samples
- ✅ `full_brd_integration/` - Full integration test inputs

#### Missing Test Coverage

1. **PDF Scanning Tests**
   - Implement tests for scanned PDFs (OCR scenarios)
   - Add test files to `__tests__/.inputs/pdf_tests/`
   - Verify text extraction from image-based PDFs

2. **Expanded Error Handling Tests**
   - Implement corrupt file scenario tests
   - Implement file permission error tests
   - Implement large file handling tests
   - Implement network drive scenario tests (if applicable)

3. **Template Validation Tests**
   - Implement template structure preservation tests
   - Implement custom template tests
   - Implement predefined template tests (SAFE BRD, Legacy Inscore)

4. **Complex Formatting Tests**
   - Implement Word document tests with nested structures, styles, and complex tables
   - Implement Excel tests with named ranges and multiple sheets
   - Implement mixed-format folder tests

#### Implementation Notes

**Test Location:**
- Add new tests to `plugins/AIDLC-business-analyst/__tests__/`
- Follow existing test naming pattern: `{feature}_{type}.test`

**Test Input Files:**
- Store in `__tests__/.inputs/{test_category}/`
- Use descriptive folder names (e.g., `pdf_tests/`, `complex_formatting/`)

**Test Structure:**
Use existing test format:
```
=== TEST: Test Name ===
DESCRIPTION: ...
--- ACCEPTANCE CRITERIA ---
--- PROMPT ---
--- EXPECTED KEYWORDS ---
--- END TEST ---
```

#### Related Files

- Test suite location: `plugins/AIDLC-business-analyst/__tests__/`
- Test inputs: `plugins/AIDLC-business-analyst/__tests__/.inputs/`
- Test documentation: `plugins/AIDLC-business-analyst/__tests__/TEST_SUITE_README.md`
- Acceptance criteria: `plugins/AIDLC-business-analyst/acceptance-criteria.md`

#### Technical Dependencies

- OCR library for scanned PDF testing (if not already available)
- Sample scanned PDF files for testing
- Sample corrupt files for error handling tests

#### Estimated Effort

**Remaining Work:** 1 story point
**Original Estimate:** 1 story point (US774992)
**Effort Breakdown:**
- Set up test infrastructure: 1 hour
- Implement PDF scanning tests: 3 hours
- Implement expanded error handling tests: 2 hours
- Implement template validation tests: 1 hour
- Implement complex formatting tests: 1 hour
- **Total:** ~8 hours

#### Implementation Tasks

1. **Set up test infrastructure:**
   - Create `__tests__/.inputs/pdf_tests/` directory
   - Create `__tests__/.inputs/complex_formatting/` directory
   - Add sample scanned PDF files
   - Add sample corrupt files

2. **Implement test cases:**
   - Write PDF scanning test cases in new test file
   - Write expanded error handling test cases
   - Write template validation test cases
   - Write complex formatting test cases

3. **Validation:**
   - Run all tests with diverse inputs
   - Verify 100% acceptance criteria coverage

---

## Version History

| Date | Version | Changes |
|------|---------|---------|
| 2026-02-16 | 1.0.0 | Initial TODO created from Rally user story analysis |

---

## References

- **Rally Feature:** F117572 - BRD Generation from Documents
- **Rally User Story:** US774992 - Create comprehensive test suite for BRD generation with diverse file types
- **Implementation Doc:** `plugins/AIDLC-business-analyst/docs/US774990.md`
- **Acceptance Criteria:** `plugins/AIDLC-business-analyst/acceptance-criteria.md`
- **Test Suite README:** `plugins/AIDLC-business-analyst/__tests__/TEST_SUITE_README.md`
