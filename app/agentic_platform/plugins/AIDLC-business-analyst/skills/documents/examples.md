# Document Converter Examples

## Excel Operations

### List sheets in a workbook
```bash
python convert.py xlsx-sheets report.xlsx
```
Output:
```
Sheets in report.xlsx:
  - Summary
  - Q1 Data
  - Q2 Data
  - Raw Data
```

### Get sheet dimensions
```bash
python convert.py xlsx-info report.xlsx
```
Output:
```
Sheet information for report.xlsx:

  Summary:
    Dimensions: A1:F25
    Rows: 25, Columns: 6 (F)

  Q1 Data:
    Dimensions: A1:M150
    Rows: 150, Columns: 13 (M)
```

### Convert specific sheet
```bash
python convert.py xlsx2table report.xlsx --sheet 'Q1 Data'
```

### Convert multiple sheets
```bash
python convert.py xlsx2table report.xlsx --sheets 'Summary,Q1 Data'
```

### Extract specific range
```bash
python convert.py xlsx2table data.xlsx --range 'B2:F20'
```

### Extract range from specific sheet
```bash
python convert.py xlsx2table data.xlsx --range 'Sales!A1:D50' sales.md
```

### Data without headers
```bash
python convert.py xlsx2table raw_data.xlsx --range 'A1:C100' --no-header
```

### Export sheet to CSV
```bash
python convert.py xlsx2csv report.xlsx --sheet 'Sales'
```

### Export range to CSV with semicolon delimiter
```bash
python convert.py xlsx2csv data.xlsx --range 'B2:F50' --delimiter ';' output.csv
```

### Convert CSV to Excel
```bash
python convert.py csv2xlsx data.csv data.xlsx
```

### CSV with tab delimiter
```bash
python convert.py csv2xlsx data.tsv output.xlsx --delimiter '\t'
```

## Word Documents

### Convert README to Word
```bash
python convert.py md2docx README.md README.docx
```
**Note:** Uses GATHER-help:md-to-word skill for enhanced formatting, TOC support, and better table handling.

After conversion, add a Table of Contents in Word:
1. Open README.docx in Microsoft Word
2. Click at the beginning → **References** → **Table of Contents** → **Automatic Table**

### Extract Word document to Markdown
```bash
python convert.py docx2md report.docx report.md
```

## PDF Documents

### Generate PDF from markdown
```bash
python convert.py md2pdf documentation.md documentation.pdf
```

### Extract PDF text to markdown
```bash
python convert.py pdf2md scanned_doc.pdf extracted.md
```

## PowerPoint Presentations

### Create slides from markdown outline
```bash
python convert.py md2pptx outline.md presentation.pptx
```

Input markdown structure:
```markdown
# Quarterly Review

## Revenue Overview

- Q1: $1.2M
- Q2: $1.5M
- Q3: $1.8M

## Key Metrics

| Metric | Value |
|--------|-------|
| Users  | 50K   |
| Growth | 25%   |
```

### Extract presentation to markdown
```bash
python convert.py pptx2md slides.pptx notes.md
```

## Outlook Emails

### Read MSG file as markdown
```bash
python convert.py msg2md meeting_invite.msg meeting.md
```

### Convert to standard email format
```bash
python convert.py msg2eml outlook_email.msg portable.eml
```

### Extract all attachments
```bash
python convert.py msg-extract email_with_files.msg ./downloads/
```

## Programmatic Usage

```python
from convert import DocumentConverter

converter = DocumentConverter()

# List sheets
sheets = converter.list_sheets("report.xlsx")

# Get sheet info
info = converter.get_sheet_info("report.xlsx")

# Extract specific range
md = converter.xlsx_to_markdown_table(
    "data.xlsx",
    output_path=None,
    sheets=["Sales"],
    range_notation="B2:F20",
    no_header=False
)

# Markdown to PDF
converter.md_to_pdf("report.md", "report.pdf")

# Read Outlook email
content = converter.msg_to_md("email.msg", None)
```
