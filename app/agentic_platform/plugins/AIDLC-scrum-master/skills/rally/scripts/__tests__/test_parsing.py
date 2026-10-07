"""
Unit tests for rally_api.parsing module.

Tests HTML and date parsing utilities including:
- HTML text extraction
- Date parsing from various formats
- Extracting dates from HTML descriptions
- Markdown to HTML conversion
- Rally link extraction
- Description format validation
"""

import pytest
from datetime import datetime
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from rally_api import parsing


class TestHTMLTextExtractor:
    """Tests for HTMLTextExtractor class."""

    def test_extract_plain_text(self):
        """Test extracting plain text from simple HTML."""
        html = "<p>This is a test</p>"

        extractor = parsing.HTMLTextExtractor()
        extractor.feed(html)
        text = extractor.get_text()

        assert "This is a test" in text

    def test_extract_text_with_multiple_paragraphs(self):
        """Test extracting text from multiple paragraphs."""
        html = "<p>First paragraph</p><p>Second paragraph</p>"

        extractor = parsing.HTMLTextExtractor()
        extractor.feed(html)
        text = extractor.get_text()

        assert "First paragraph" in text
        assert "Second paragraph" in text

    def test_extract_text_from_list(self):
        """Test extracting text from HTML lists."""
        html = "<ul><li>Item 1</li><li>Item 2</li></ul>"

        extractor = parsing.HTMLTextExtractor()
        extractor.feed(html)
        text = extractor.get_text()

        assert "Item 1" in text
        assert "Item 2" in text

    def test_extract_table_structure(self):
        """Test extracting table data preserving structure."""
        html = """
        <table>
        <tr><th>Date</th><th>Milestone</th></tr>
        <tr><td>Jan 15, 2026</td><td>First Release</td></tr>
        </table>
        """

        extractor = parsing.HTMLTextExtractor()
        extractor.feed(html)
        tables = extractor.get_tables()

        assert len(tables) == 1
        assert len(tables[0]) == 2  # 2 rows
        assert "Date" in tables[0][0]
        assert "Jan 15, 2026" in tables[0][1]

    def test_strip_html_tags(self):
        """Test that HTML tags are removed."""
        html = "<strong>Bold</strong> and <em>italic</em>"

        extractor = parsing.HTMLTextExtractor()
        extractor.feed(html)
        text = extractor.get_text()

        assert "<strong>" not in text
        assert "Bold" in text
        assert "italic" in text


class TestDateParsing:
    """Tests for date parsing functions."""

    def test_parse_date_short_month(self):
        """Test parsing date with short month name."""
        date_str = "Dec 17, 2025"

        result = parsing.parse_date(date_str)

        assert result is not None
        assert result.year == 2025
        assert result.month == 12
        assert result.day == 17

    def test_parse_date_full_month(self):
        """Test parsing date with full month name."""
        date_str = "December 17, 2025"

        result = parsing.parse_date(date_str)

        assert result is not None
        assert result.year == 2025
        assert result.month == 12
        assert result.day == 17

    def test_parse_date_iso_format(self):
        """Test parsing ISO format date."""
        date_str = "2025-12-17"

        result = parsing.parse_date(date_str)

        assert result is not None
        assert result.year == 2025
        assert result.month == 12
        assert result.day == 17

    def test_parse_date_slash_format(self):
        """Test parsing date with slashes."""
        date_str = "12/17/2025"

        result = parsing.parse_date(date_str)

        assert result is not None
        assert result.year == 2025

    def test_parse_date_invalid(self):
        """Test parsing invalid date returns None."""
        date_str = "invalid date"

        result = parsing.parse_date(date_str)

        assert result is None

    def test_parse_date_strips_whitespace(self):
        """Test date parsing handles extra whitespace."""
        date_str = "  Jan 2, 2026  "

        result = parsing.parse_date(date_str)

        assert result is not None
        assert result.year == 2026


class TestExtractDatesFromDescription:
    """Tests for extracting dates from HTML descriptions."""

    def test_extract_dates_from_table(self):
        """Test extracting dates from HTML table."""
        html = """
        <table>
        <tr><th>Date</th><th>Milestone</th></tr>
        <tr><td>Dec 17, 2025</td><td>First Release</td></tr>
        <tr><td>Jan 15, 2026</td><td>Second Release</td></tr>
        </table>
        """

        results = parsing.extract_dates_from_description(html)

        assert len(results) >= 2
        assert any(r['date'].year == 2025 for r in results)
        assert any(r['date'].year == 2026 for r in results)

    def test_extract_dates_with_milestone_info(self):
        """Test extracting dates preserves milestone information."""
        html = """
        <table>
        <tr><th>Date</th><th>Milestone</th><th>Owner</th></tr>
        <tr><td>Dec 17, 2025</td><td>Beta Release</td><td>Chris</td></tr>
        </table>
        """

        results = parsing.extract_dates_from_description(html)

        assert len(results) > 0
        assert results[0]['milestone'] == 'Beta Release'
        assert results[0]['owner'] == 'Chris'

    def test_extract_dates_with_ticket_ids(self):
        """Test extracting Rally ticket IDs from date rows."""
        html = """
        <table>
        <tr><th>Date</th><th>Task</th><th>Ticket</th></tr>
        <tr><td>Dec 17, 2025</td><td>Complete US12345</td><td>US12345</td></tr>
        </table>
        """

        results = parsing.extract_dates_from_description(html)

        assert len(results) > 0
        assert 'US12345' in results[0]['tickets']

    def test_extract_dates_from_text(self):
        """Test extracting dates from plain text patterns."""
        html = "<p>This must be completed by Jan 15, 2026</p>"

        results = parsing.extract_dates_from_description(html)

        assert len(results) > 0
        assert any(r['date'].year == 2026 and r['date'].month == 1 for r in results)

    def test_extract_dates_empty_description(self):
        """Test extracting dates from empty description."""
        results = parsing.extract_dates_from_description("")

        assert results == []

    def test_extract_dates_sorted_by_date(self):
        """Test extracted dates are sorted chronologically."""
        html = """
        <table>
        <tr><th>Date</th><th>Milestone</th></tr>
        <tr><td>Dec 17, 2026</td><td>Last</td></tr>
        <tr><td>Jan 15, 2026</td><td>First</td></tr>
        <tr><td>Jun 10, 2026</td><td>Middle</td></tr>
        </table>
        """

        results = parsing.extract_dates_from_description(html)

        assert len(results) >= 3
        for i in range(len(results) - 1):
            assert results[i]['date'] <= results[i + 1]['date']


class TestMarkdownToHTML:
    """Tests for markdown to HTML conversion."""

    def test_convert_bold_with_asterisks(self):
        """Test converting **bold** to <strong>."""
        markdown = "This is **bold text**"

        html = parsing.markdown_to_html(markdown)

        assert "<strong>bold text</strong>" in html

    def test_convert_bold_with_underscores(self):
        """Test converting __bold__ to <strong>."""
        markdown = "This is __bold text__"

        html = parsing.markdown_to_html(markdown)

        assert "<strong>bold text</strong>" in html

    def test_convert_inline_code(self):
        """Test converting `code` to <code>."""
        markdown = "Use `print()` function"

        html = parsing.markdown_to_html(markdown)

        assert "<code>print()</code>" in html

    def test_convert_headers(self):
        """Test converting markdown headers to HTML."""
        markdown = "## Main Header\n### Sub Header"

        html = parsing.markdown_to_html(markdown)

        assert "<h3>Main Header</h3>" in html
        assert "<h3>Sub Header</h3>" in html

    def test_convert_bullet_list(self):
        """Test converting markdown bullet list to HTML."""
        markdown = "- Item 1\n- Item 2\n- Item 3"

        html = parsing.markdown_to_html(markdown)

        assert "<ul>" in html
        assert "<li>Item 1</li>" in html
        assert "<li>Item 2</li>" in html
        assert "</ul>" in html

    def test_convert_asterisk_list(self):
        """Test converting * bullet list to HTML."""
        markdown = "* Item 1\n* Item 2"

        html = parsing.markdown_to_html(markdown)

        assert "<ul>" in html
        assert "<li>Item 1</li>" in html

    def test_convert_paragraphs(self):
        """Test wrapping text in <p> tags."""
        markdown = "This is a paragraph"

        html = parsing.markdown_to_html(markdown)

        assert "<p>This is a paragraph</p>" in html

    def test_convert_empty_text(self):
        """Test converting empty markdown returns empty string."""
        result = parsing.markdown_to_html("")

        assert result == ""

    def test_convert_mixed_formatting(self):
        """Test converting markdown with mixed formatting."""
        markdown = """## Header
**Bold text** and `code`
- List item 1
- List item 2"""

        html = parsing.markdown_to_html(markdown)

        assert "<h3>Header</h3>" in html
        assert "<strong>Bold text</strong>" in html
        assert "<code>code</code>" in html
        assert "<ul>" in html
        assert "<li>List item 1</li>" in html


class TestValidateDescriptionFormat:
    """Tests for description format validation."""

    def test_validate_html_format_valid(self):
        """Test validating proper HTML format."""
        description = "<p>This is HTML</p><ul><li>Item</li></ul>"

        is_valid, message = parsing.validate_description_format(description)

        assert is_valid is True
        assert message == ""

    def test_validate_empty_description(self):
        """Test validating empty description is valid."""
        is_valid, message = parsing.validate_description_format("")

        assert is_valid is True

    def test_validate_markdown_format_invalid(self):
        """Test detecting markdown format as invalid."""
        description = "## Header\n**Bold**\n- List item"

        is_valid, message = parsing.validate_description_format(description)

        assert is_valid is False
        assert "markdown" in message.lower()
        assert "HTML" in message

    def test_validate_suggests_conversion(self):
        """Test validation message suggests conversion function."""
        description = "## Header"

        is_valid, message = parsing.validate_description_format(description)

        assert "markdown_to_html" in message

    def test_validate_mixed_format(self):
        """Test mixed HTML and markdown is accepted (HTML present)."""
        description = "<p>HTML paragraph</p>\n## Markdown header"

        is_valid, message = parsing.validate_description_format(description)

        # If HTML is present, it's considered valid even with markdown
        assert is_valid is True


class TestExtractRallyLinks:
    """Tests for extracting Rally ticket links."""

    def test_extract_rally_url(self):
        """Test extracting full Rally URL."""
        html = '<a href="https://rally1.rallydev.com/slm/webservice/v2.0/userstory/12345">US12345</a>'

        results = parsing.extract_rally_links(html)

        assert len(results) > 0
        assert results[0]['url'] is not None
        assert "userstory" in results[0]['url']

    def test_extract_formatted_id_without_url(self):
        """Test extracting FormattedID without full URL."""
        html = "<p>This relates to US12345 and TA67890</p>"

        results = parsing.extract_rally_links(html)

        assert len(results) >= 2
        formatted_ids = [r['formatted_id'] for r in results]
        assert 'US12345' in formatted_ids
        assert 'TA67890' in formatted_ids

    def test_extract_various_work_item_types(self):
        """Test extracting different Rally work item types."""
        html = "<p>Tickets: US12345, TA67890, DE11111, F22222</p>"

        results = parsing.extract_rally_links(html)

        formatted_ids = [r['formatted_id'] for r in results]
        assert 'US12345' in formatted_ids
        assert 'TA67890' in formatted_ids
        assert 'DE11111' in formatted_ids
        assert 'F22222' in formatted_ids

    def test_extract_no_duplicates(self):
        """Test extracting links doesn't return duplicates."""
        html = "<p>US12345 is mentioned twice: US12345</p>"

        results = parsing.extract_rally_links(html)

        formatted_ids = [r['formatted_id'] for r in results]
        assert formatted_ids.count('US12345') == 1

    def test_extract_from_empty_description(self):
        """Test extracting from empty description."""
        results = parsing.extract_rally_links("")

        assert results == []

    def test_extract_case_insensitive(self):
        """Test extracting ticket IDs is case insensitive."""
        html = "<p>Tickets: us12345, TA67890</p>"

        results = parsing.extract_rally_links(html)

        assert len(results) >= 2
        # Should be normalized to uppercase
        formatted_ids = [r['formatted_id'] for r in results]
        assert 'US12345' in formatted_ids or 'us12345' in formatted_ids
