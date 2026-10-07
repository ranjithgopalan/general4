"""Rally API HTML and date parsing utilities.

Provides functions for:
- Extracting plain text from HTML
- Parsing various date formats
- Extracting dates from descriptions
- Extracting Rally ticket links
"""

import re
from datetime import datetime
from html.parser import HTMLParser
from typing import Optional


class HTMLTextExtractor(HTMLParser):
    """Extract plain text from HTML, preserving table structure."""

    def __init__(self):
        super().__init__()
        self.text_parts = []
        self.in_table = False
        self.current_row = []
        self.tables = []

    def handle_starttag(self, tag, attrs):
        if tag == 'table':
            self.in_table = True
            self.tables.append([])
        elif tag == 'tr' and self.in_table:
            self.current_row = []
        elif tag in ('li', 'p', 'br'):
            self.text_parts.append('\n')

    def handle_endtag(self, tag):
        if tag == 'table':
            self.in_table = False
        elif tag == 'tr' and self.tables:
            self.tables[-1].append(self.current_row)
            self.current_row = []

    def handle_data(self, data):
        text = data.strip()
        if text:
            if self.in_table and self.current_row is not None:
                self.current_row.append(text)
            else:
                self.text_parts.append(text)

    def get_text(self):
        return ' '.join(self.text_parts)

    def get_tables(self):
        return self.tables


def parse_date(date_str: str) -> Optional[datetime]:
    """
    Parse various date formats from Rally descriptions.

    Supports:
    - Dec 17, 2025
    - December 17, 2025
    - 2025-12-17
    - 12/17/2025
    - Jan 2, 2026

    Args:
        date_str: Date string to parse

    Returns:
        datetime object or None if parsing fails
    """
    date_str = date_str.strip()

    formats = [
        "%b %d, %Y",      # Dec 17, 2025
        "%B %d, %Y",      # December 17, 2025
        "%Y-%m-%d",       # 2025-12-17
        "%m/%d/%Y",       # 12/17/2025
        "%d/%m/%Y",       # 17/12/2025
        "%b %d %Y",       # Dec 17 2025
    ]

    for fmt in formats:
        try:
            return datetime.strptime(date_str, fmt)
        except ValueError:
            continue

    return None


def extract_dates_from_description(html_description: str) -> list[dict]:
    """
    Extract dates and milestones from a Rally description HTML.

    Looks for:
    - Tables with date columns (Key Dates format)
    - Inline dates in text

    Args:
        html_description: HTML description from Rally

    Returns:
        List of dicts with: milestone, date, owner, tickets
    """
    if not html_description:
        return []

    results = []

    # Parse HTML
    parser = HTMLTextExtractor()
    parser.feed(html_description)
    tables = parser.get_tables()

    # Process tables for Key Dates format
    for table in tables:
        if not table:
            continue

        # Check if this looks like a dates table
        header = table[0] if table else []
        header_lower = [h.lower() for h in header]

        # Find column indices
        date_col = None
        milestone_col = None
        owner_col = None
        ticket_col = None

        for i, h in enumerate(header_lower):
            if 'date' in h:
                date_col = i
            elif 'milestone' in h or 'action' in h or 'task' in h:
                milestone_col = i
            elif 'owner' in h or 'assigned' in h:
                owner_col = i
            elif 'ticket' in h or 'link' in h or 'ref' in h:
                ticket_col = i

        if date_col is None:
            continue

        # Process data rows
        for row in table[1:]:
            if len(row) <= date_col:
                continue

            date_str = row[date_col]
            parsed_date = parse_date(date_str)

            if parsed_date:
                result = {
                    'date': parsed_date,
                    'date_str': date_str,
                    'milestone': row[milestone_col] if milestone_col is not None and len(row) > milestone_col else None,
                    'owner': row[owner_col] if owner_col is not None and len(row) > owner_col else None,
                    'tickets': [],
                }

                # Extract ticket IDs from the row
                row_text = ' '.join(row)
                ticket_matches = re.findall(r'(TA\d+|US\d+|DE\d+|F\d+)', row_text, re.IGNORECASE)
                result['tickets'] = [t.upper() for t in ticket_matches]

                results.append(result)

    # Also look for dates in plain text
    text = parser.get_text()
    date_patterns = [
        r'by\s+((?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{1,2},?\s+\d{4})',
        r'on\s+((?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{1,2},?\s+\d{4})',
        r'due\s+((?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{1,2},?\s+\d{4})',
    ]

    for pattern in date_patterns:
        matches = re.findall(pattern, text, re.IGNORECASE)
        for match in matches:
            parsed = parse_date(match)
            if parsed and not any(r['date'] == parsed for r in results):
                results.append({
                    'date': parsed,
                    'date_str': match,
                    'milestone': None,
                    'owner': None,
                    'tickets': [],
                })

    # Sort by date
    results.sort(key=lambda x: x['date'])

    return results


def markdown_to_html(markdown_text: str) -> str:
    """
    Convert markdown description to HTML format for Rally.

    Handles:
    - ** bold ** -> <strong>bold</strong>
    - __ bold __ -> <strong>bold</strong>
    - `code` -> <code>code</code>
    - Bullet lists (- or *) -> <ul><li>
    - Headers (## or ###) -> <h3>
    - Paragraphs -> <p>

    Args:
        markdown_text: Markdown formatted text

    Returns:
        HTML formatted text
    """
    if not markdown_text:
        return ""

    html = markdown_text

    # Convert bold (**text** or __text__) to <strong>
    html = re.sub(r'\*\*([^*]+)\*\*', r'<strong>\1</strong>', html)
    html = re.sub(r'__([^_]+)__', r'<strong>\1</strong>', html)

    # Convert inline code (`text`) to <code>
    html = re.sub(r'`([^`]+)`', r'<code>\1</code>', html)

    # Split into lines for list/header processing
    lines = html.split('\n')
    result_lines = []
    in_list = False

    i = 0
    while i < len(lines):
        line = lines[i].strip()

        # Skip empty lines at start of lists
        if not line:
            if in_list:
                result_lines.append('</ul>')
                in_list = False
            result_lines.append('')
            i += 1
            continue

        # Headers (## or ###)
        if line.startswith('###'):
            if in_list:
                result_lines.append('</ul>')
                in_list = False
            result_lines.append(f'<h3>{line[3:].strip()}</h3>')
            i += 1
            continue
        elif line.startswith('##'):
            if in_list:
                result_lines.append('</ul>')
                in_list = False
            result_lines.append(f'<h3>{line[2:].strip()}</h3>')
            i += 1
            continue

        # Bullet lists (- or *)
        if line.startswith('- ') or line.startswith('* '):
            if not in_list:
                result_lines.append('<ul>')
                in_list = True
            # Remove the bullet and add as list item
            content = line[2:].strip()
            result_lines.append(f'<li>{content}</li>')
            i += 1
            continue

        # Regular paragraph
        if in_list:
            result_lines.append('</ul>')
            in_list = False

        # Wrap non-empty lines in <p> tags
        if line:
            result_lines.append(f'<p>{line}</p>')

        i += 1

    # Close any open lists
    if in_list:
        result_lines.append('</ul>')

    # Join and clean up
    html = '\n'.join(result_lines)

    # Clean up extra whitespace
    html = re.sub(r'\n\n+', '\n\n', html)

    return html.strip()


def validate_description_format(description: str) -> tuple[bool, str]:
    """
    Validate that a Rally description uses HTML format, not markdown.

    Args:
        description: Description text to validate

    Returns:
        Tuple of (is_valid, message)
        - is_valid: True if HTML format or empty, False if markdown detected
        - message: Error message if not valid, empty string if valid
    """
    if not description:
        return True, ""

    # Check for HTML tags
    has_html = any(tag in description for tag in ['<p>', '<h3>', '<ul>', '<strong>', '<li>'])

    # Check for markdown patterns
    markdown_patterns = ['##', '**', '- ', '* ', '```']
    has_markdown = any(pattern in description for pattern in markdown_patterns)

    if has_markdown and not has_html:
        return False, (
            "Description contains markdown formatting instead of HTML. "
            "Use markdown_to_html() to convert, or see templates/ directory for HTML templates."
        )

    return True, ""


def extract_rally_links(html_description: str) -> list[dict]:
    """
    Extract Rally ticket links from HTML description.

    Args:
        html_description: HTML description from Rally

    Returns:
        List of dicts with: id, type, url
    """
    if not html_description:
        return []

    results = []

    # Find Rally URLs
    url_pattern = r'href="(https://rally1\.rallydev\.com/[^"]*/(task|userstory|defect|feature)/(\d+))"'
    matches = re.findall(url_pattern, html_description, re.IGNORECASE)

    for url, item_type, obj_id in matches:
        # Get the FormattedID from the URL pattern
        id_pattern = r'(TA|US|DE|F)\d+'
        id_match = re.search(id_pattern, html_description[html_description.find(url):html_description.find(url)+200])

        results.append({
            'url': url,
            'type': item_type,
            'object_id': obj_id,
            'formatted_id': id_match.group(0) if id_match else None,
        })

    # Also find FormattedIDs without full URLs
    id_pattern = r'\b(TA\d{5,}|US\d{5,}|DE\d{5,}|F\d{5,})\b'
    id_matches = re.findall(id_pattern, html_description, re.IGNORECASE)

    existing_ids = {r['formatted_id'] for r in results if r['formatted_id']}
    for formatted_id in id_matches:
        if formatted_id.upper() not in existing_ids:
            results.append({
                'url': None,
                'type': None,
                'object_id': None,
                'formatted_id': formatted_id.upper(),
            })
            existing_ids.add(formatted_id.upper())

    return results
