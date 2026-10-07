---
name: document-convert
description: Converts between Markdown and Office formats (DOCX, XLSX, PDF, PPTX, MSG). Use when converting markdown to Word/PDF/PowerPoint, extracting content from Office documents, or reading Outlook MSG files. Markdown to Word conversion uses GATHER-help:md-to-word skill.
---

# Document Conversion Skill

Convert between Markdown and various document formats including Microsoft Office, PDF, and Outlook MSG files.

**Note:** Markdown to Word (md2docx) conversions delegate to the `GATHER-help:md-to-word` skill for enhanced formatting, TOC support, and better table handling.

## Supported Conversions

| From | To | Use Case |
|------|-----|----------|
| Markdown | DOCX | Create Word documents from markdown files |
| DOCX | Markdown | Extract content from Word documents |
| Markdown tables | XLSX | Export tables to Excel spreadsheets |
| XLSX | Markdown | Convert spreadsheet data to markdown tables |
| XLSX | CSV | Export Excel sheet/range to CSV |
| CSV | XLSX | Convert CSV to Excel spreadsheet |
| Markdown | PDF | Create PDF documents from markdown files |
| PDF | Markdown | Extract text content from PDF files |
| Markdown | PPTX | Create PowerPoint presentations from markdown |
| PPTX | Markdown | Extract content from PowerPoint presentations |
| MSG | Markdown | Extract email content from Outlook MSG files |
| MSG | EML | Convert Outlook MSG to standard EML format |

## Quick Start

```bash
# Run conversions (dependencies auto-install on first use)
~/.claude/skills/AIDLC-business-analyst/skills/documents/.venv/bin/python ~/.claude/skills/AIDLC-business-analyst/skills/documents/scripts/convert.py <command> <input> [output]
```

The script automatically checks for required dependencies based on the command and installs any missing packages before running.

## Commands

### Markdown to DOCX

```bash
~/.claude/skills/AIDLC-business-analyst/skills/documents/.venv/bin/python ~/.claude/skills/AIDLC-business-analyst/skills/documents/scripts/convert.py md2docx input.md output.docx
```

**Uses GATHER-help:md-to-word skill** for enhanced conversion with:
- Headings (H1-H6) → Word heading styles with proper TOC support
- Bold, italic, code → Character formatting
- Lists → Word bullet/numbered lists with proper indentation
- Code blocks → Monospace formatting with terminal-style (white on black) background
- Tables → Native Word tables with proper styling and borders
- Blockquotes → Quote style formatting
- Horizontal rules → Page breaks
- Page numbers in footer
- Graceful error handling for locked files

After conversion, you can add a clickable Table of Contents in Word:
1. Open the .docx file in Microsoft Word
2. Click at the beginning of the document
3. Go to: **References** → **Table of Contents** → **Automatic Table**
4. Word will generate a TOC with clickable links from all headings

**Requirements:** The GATHER-help plugin must be installed with the md-to-word skill.

### DOCX to Markdown

```bash
~/.claude/skills/AIDLC-business-analyst/skills/documents/.venv/bin/python ~/.claude/skills/AIDLC-business-analyst/skills/documents/scripts/convert.py docx2md input.docx output.md
```

Extracts content from Word documents:
- Headings → Markdown headers
- Paragraphs → Plain text with formatting
- Tables → Markdown tables
- Lists → Markdown lists

### Markdown Table to XLSX

```bash
~/.claude/skills/AIDLC-business-analyst/skills/documents/.venv/bin/python ~/.claude/skills/AIDLC-business-analyst/skills/documents/scripts/convert.py table2xlsx input.md output.xlsx
```

Extracts markdown tables and creates Excel spreadsheet:
- First row becomes header with bold formatting
- Auto-adjusts column widths
- Multiple tables → Multiple sheets

### XLSX to Markdown Table

```bash
~/.claude/skills/AIDLC-business-analyst/skills/documents/.venv/bin/python ~/.claude/skills/AIDLC-business-analyst/skills/documents/scripts/convert.py xlsx2table input.xlsx output.md [options]
```

Options:
- `--sheet NAME` - Convert only this sheet
- `--sheets A,B,C` - Convert multiple sheets (comma-separated)
- `--range A1:D10` - Extract specific cell range
- `--range Sheet1!B2:F20` - Range with sheet name
- `--no-header` - Don't treat first row as header

Converts Excel data to markdown tables:
- First row treated as headers (unless --no-header)
- Proper column alignment
- Multiple sheets → Multiple tables

### List Excel Sheets

```bash
~/.claude/skills/AIDLC-business-analyst/skills/documents/.venv/bin/python ~/.claude/skills/AIDLC-business-analyst/skills/documents/scripts/convert.py xlsx-sheets input.xlsx
```

Lists all sheet names in the workbook.

### Excel Sheet Info

```bash
~/.claude/skills/AIDLC-business-analyst/skills/documents/.venv/bin/python ~/.claude/skills/AIDLC-business-analyst/skills/documents/scripts/convert.py xlsx-info input.xlsx
```

Shows dimensions and data range for each sheet.

### Excel to CSV

```bash
~/.claude/skills/AIDLC-business-analyst/skills/documents/.venv/bin/python ~/.claude/skills/AIDLC-business-analyst/skills/documents/scripts/convert.py xlsx2csv input.xlsx output.csv [options]
```

Options:
- `--sheet NAME` - Convert only this sheet (default: first sheet)
- `--range A1:D10` - Extract specific cell range
- `--delimiter ';'` - Use custom delimiter (default: comma)

### CSV to Excel

```bash
~/.claude/skills/AIDLC-business-analyst/skills/documents/.venv/bin/python ~/.claude/skills/AIDLC-business-analyst/skills/documents/scripts/convert.py csv2xlsx input.csv output.xlsx [options]
```

Options:
- `--delimiter ';'` - Specify CSV delimiter (default: comma)
- `--no-header` - Don't bold the first row

### Markdown to PDF

```bash
~/.claude/skills/AIDLC-business-analyst/skills/documents/.venv/bin/python ~/.claude/skills/AIDLC-business-analyst/skills/documents/scripts/convert.py md2pdf input.md output.pdf
```

Converts markdown to a formatted PDF document with:
- Headings with proper styling
- Bold, italic, code formatting
- Lists and nested lists
- Code blocks with syntax highlighting
- Tables with borders
- Automatic page breaks

### PDF to Markdown

```bash
~/.claude/skills/AIDLC-business-analyst/skills/documents/.venv/bin/python ~/.claude/skills/AIDLC-business-analyst/skills/documents/scripts/convert.py pdf2md input.pdf output.md
```

Extracts content from PDF files:
- Text extraction with paragraph detection
- Table detection and conversion to markdown tables
- Preserves document structure where possible
- Handles multi-column layouts

### Markdown to PowerPoint

```bash
~/.claude/skills/AIDLC-business-analyst/skills/documents/.venv/bin/python ~/.claude/skills/AIDLC-business-analyst/skills/documents/scripts/convert.py md2pptx input.md output.pptx
```

Creates PowerPoint presentations from markdown:
- H1 headers → Title slides
- H2 headers → Section slides
- H3+ headers → Content slide titles
- Bullet points → Slide bullet lists
- Code blocks → Formatted code boxes
- Tables → PowerPoint tables
- Images → Embedded images

Markdown structure for presentations:
```markdown
# Presentation Title

## Section 1

### Slide Title

- Bullet point 1
- Bullet point 2
  - Nested bullet

### Another Slide

Content paragraph here.

| Col1 | Col2 |
|------|------|
| A    | B    |
```

### PowerPoint to Markdown

```bash
~/.claude/skills/AIDLC-business-analyst/skills/documents/.venv/bin/python ~/.claude/skills/AIDLC-business-analyst/skills/documents/scripts/convert.py pptx2md input.pptx output.md
```

Extracts content from PowerPoint presentations:
- Title slides → H1 headers
- Slide titles → H2/H3 headers
- Bullet lists → Markdown lists
- Tables → Markdown tables
- Speaker notes → Blockquotes (optional)

### MSG to Markdown

```bash
~/.claude/skills/AIDLC-business-analyst/skills/documents/.venv/bin/python ~/.claude/skills/AIDLC-business-analyst/skills/documents/scripts/convert.py msg2md input.msg output.md
```

Extracts email content from Outlook MSG files:
- Email headers (From, To, CC, Subject, Date)
- Email body (HTML converted to markdown or plain text)
- Attachment list with filenames and sizes
- Embedded images extracted to separate files

Output format:
```markdown
# Subject Line

| Field | Value |
|-------|-------|
| From | sender@example.com |
| To | recipient@example.com |
| Date | 2025-01-15 10:30:00 |

## Body

Email content here...

## Attachments

- document.pdf (245 KB)
- image.png (128 KB)
```

### MSG to EML

```bash
~/.claude/skills/AIDLC-business-analyst/skills/documents/.venv/bin/python ~/.claude/skills/AIDLC-business-analyst/skills/documents/scripts/convert.py msg2eml input.msg output.eml
```

Converts Outlook MSG to standard EML format:
- Preserves all email headers
- Maintains attachments
- Compatible with other email clients
- Standard RFC 822 format

### Extract MSG Attachments

```bash
~/.claude/skills/AIDLC-business-analyst/skills/documents/.venv/bin/python ~/.claude/skills/AIDLC-business-analyst/skills/documents/scripts/convert.py msg-extract input.msg output_dir/
```

Extracts all attachments from MSG file:
- Saves attachments to specified directory
- Preserves original filenames
- Reports extraction summary

## Examples

### Convert a README to Word

```bash
~/.claude/skills/AIDLC-business-analyst/skills/documents/.venv/bin/python ~/.claude/skills/AIDLC-business-analyst/skills/documents/scripts/convert.py md2docx README.md README.docx
```

### Extract tables from markdown to Excel

```bash
~/.claude/skills/AIDLC-business-analyst/skills/documents/.venv/bin/python ~/.claude/skills/AIDLC-business-analyst/skills/documents/scripts/convert.py table2xlsx report.md report.xlsx
```

### Convert Excel report to markdown

```bash
~/.claude/skills/AIDLC-business-analyst/skills/documents/.venv/bin/python ~/.claude/skills/AIDLC-business-analyst/skills/documents/scripts/convert.py xlsx2table data.xlsx tables.md
```

### Extract specific sheet and range

```bash
~/.claude/skills/AIDLC-business-analyst/skills/documents/.venv/bin/python ~/.claude/skills/AIDLC-business-analyst/skills/documents/scripts/convert.py xlsx2table report.xlsx --range 'Sales!B2:F50' sales.md
```

### List sheets before converting

```bash
~/.claude/skills/AIDLC-business-analyst/skills/documents/.venv/bin/python ~/.claude/skills/AIDLC-business-analyst/skills/documents/scripts/convert.py xlsx-sheets report.xlsx
~/.claude/skills/AIDLC-business-analyst/skills/documents/.venv/bin/python ~/.claude/skills/AIDLC-business-analyst/skills/documents/scripts/convert.py xlsx-info report.xlsx
```

### Generate PDF from documentation

```bash
~/.claude/skills/AIDLC-business-analyst/skills/documents/.venv/bin/python ~/.claude/skills/AIDLC-business-analyst/skills/documents/scripts/convert.py md2pdf docs/guide.md guide.pdf
```

### Extract text from PDF

```bash
~/.claude/skills/AIDLC-business-analyst/skills/documents/.venv/bin/python ~/.claude/skills/AIDLC-business-analyst/skills/documents/scripts/convert.py pdf2md report.pdf report.md
```

### Create presentation from outline

```bash
~/.claude/skills/AIDLC-business-analyst/skills/documents/.venv/bin/python ~/.claude/skills/AIDLC-business-analyst/skills/documents/scripts/convert.py md2pptx presentation_outline.md slides.pptx
```

### Extract presentation content

```bash
~/.claude/skills/AIDLC-business-analyst/skills/documents/.venv/bin/python ~/.claude/skills/AIDLC-business-analyst/skills/documents/scripts/convert.py pptx2md meeting_slides.pptx meeting_notes.md
```

### Read Outlook email

```bash
~/.claude/skills/AIDLC-business-analyst/skills/documents/.venv/bin/python ~/.claude/skills/AIDLC-business-analyst/skills/documents/scripts/convert.py msg2md important_email.msg email_content.md
```

### Convert MSG to standard email format

```bash
~/.claude/skills/AIDLC-business-analyst/skills/documents/.venv/bin/python ~/.claude/skills/AIDLC-business-analyst/skills/documents/scripts/convert.py msg2eml outlook_email.msg standard_email.eml
```

### Extract email attachments

```bash
~/.claude/skills/AIDLC-business-analyst/skills/documents/.venv/bin/python ~/.claude/skills/AIDLC-business-analyst/skills/documents/scripts/convert.py msg-extract email.msg ./attachments/
```

## Inline Usage

For quick conversions without files:

```python
import sys
sys.path.insert(0, '~/.claude/skills/AIDLC-business-analyst/skills/documents/scripts')
from convert import DocumentConverter

converter = DocumentConverter()

# Convert markdown string to docx
converter.md_to_docx("# Hello\n\nWorld", "output.docx")

# Convert xlsx to markdown table string
md_table = converter.xlsx_to_markdown_table("data.xlsx")
print(md_table)

# Convert markdown to PDF
converter.md_to_pdf("# Report\n\nContent here", "report.pdf")

# Extract PDF text
text = converter.pdf_to_markdown("document.pdf")

# Create presentation
converter.md_to_pptx("# Title\n\n## Slide 1\n\n- Point 1", "slides.pptx")

# Read MSG file
email_md = converter.msg_to_markdown("email.msg")
print(email_md)
```

## Dependencies

Dependencies are automatically installed when needed. Each command only installs its required packages:

| Command | Auto-installed packages | Notes |
|---------|------------------------|-------|
| `md2docx` | None | Uses GATHER-help:md-to-word skill |
| `docx2md` | `python-docx` | |
| `table2xlsx`, `xlsx2table` | `openpyxl` | |
| `md2pdf` | `weasyprint`, `markdown` | |
| `pdf2md` | `pdfplumber` | |
| `md2pptx`, `pptx2md` | `python-pptx` | |
| `msg2md`, `msg2eml`, `msg-extract` | `extract-msg`, `beautifulsoup4` | |

**Note:** The `md2docx` command requires the GATHER-help plugin with the md-to-word skill installed. It delegates to `~/.claude/venvs/ADLC/Scripts/md2word` (Windows) or `~/.claude/venvs/ADLC/bin/md2word` (macOS/Linux).

The skill uses its own virtual environment at `~/.claude/skills/AIDLC-business-analyst/skills/documents/.venv/`.

To force reinstall all dependencies:
```bash
~/.claude/skills/AIDLC-business-analyst/skills/documents/.venv/bin/pip install --force-reinstall -r ~/.claude/skills/AIDLC-business-analyst/skills/documents/requirements.txt
```

## Platform Notes

### PDF Generation (weasyprint)

WeasyPrint requires system dependencies for rendering:

**macOS:**
```bash
brew install pango cairo libffi
```

**Ubuntu/Debian:**
```bash
apt-get install libpango-1.0-0 libpangocairo-1.0-0 libcairo2
```

**Windows:**
WeasyPrint has limited Windows support. Consider using `md2docx` followed by Word's PDF export as an alternative.

### MSG Files

MSG file support is read-only. Creating new MSG files is not supported due to the proprietary format. Use EML format for cross-platform email file creation.
