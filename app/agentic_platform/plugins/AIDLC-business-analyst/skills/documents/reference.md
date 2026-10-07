# Document Converter Reference

## Commands

| Command | Input | Output | Description |
|---------|-------|--------|-------------|
| `md2docx` | .md | .docx | Markdown to Word (uses GATHER-help:md-to-word) |
| `docx2md` | .docx | .md | Word to Markdown |
| `table2xlsx` | .md | .xlsx | Markdown tables to Excel |
| `xlsx2table` | .xlsx | .md | Excel to Markdown tables |
| `xlsx2csv` | .xlsx | .csv | Excel to CSV |
| `csv2xlsx` | .csv | .xlsx | CSV to Excel |
| `xlsx-sheets` | .xlsx | stdout | List sheet names |
| `xlsx-info` | .xlsx | stdout | Show sheet dimensions |
| `md2pdf` | .md | .pdf | Markdown to PDF |
| `pdf2md` | .pdf | .md | PDF to Markdown |
| `md2pptx` | .md | .pptx | Markdown to PowerPoint |
| `pptx2md` | .pptx | .md | PowerPoint to Markdown |
| `msg2md` | .msg | .md | Outlook email to Markdown |
| `msg2eml` | .msg | .eml | Outlook to standard EML |
| `msg-extract` | .msg | dir/ | Extract MSG attachments |

## Excel Options (xlsx2table, xlsx2csv, csv2xlsx)

| Option | Description |
|--------|-------------|
| `--sheet NAME` | Convert only this sheet |
| `--sheets A,B,C` | Convert multiple sheets (xlsx2table only) |
| `--range A1:D10` | Extract specific cell range |
| `--range Sheet1!B2:F20` | Range with sheet name |
| `--no-header` | Don't treat first row as header |
| `--delimiter ';'` | CSV delimiter (default: comma) |

## A1 Notation

Supports standard Excel range notation:

| Format | Description |
|--------|-------------|
| `A1` | Single cell |
| `A1:D10` | Range on active/first sheet |
| `B2:F20` | Range starting at B2 |
| `Sheet1!A1:D10` | Range on specific sheet |
| `'Sheet Name'!A1:D10` | Sheet name with spaces |

Column letters: A-Z, then AA-AZ, BA-BZ, etc.

## Markdown to PowerPoint Structure

```
# Title Slide (H1)

## Content Slide Title (H2)

- Bullet point (becomes slide bullet)
  - Nested bullet (indented)

### Sub-heading (H3+, stays on current slide)

| Table | Data |  (becomes PowerPoint table)
|-------|------|
| A     | B    |
```

## MSG Output Format

```markdown
# Subject Line

| Field | Value |
|-------|-------|
| From | sender@example.com |
| To | recipient@example.com |
| Date | 2025-01-15 10:30:00 |

## Body

Email content...

## Attachments

- file.pdf (245 KB)
```

## Dependencies by Command

| Commands | Packages | Notes |
|----------|----------|-------|
| md2docx | None | Uses GATHER-help:md-to-word skill |
| docx2md | python-docx | |
| table2xlsx, xlsx2table, xlsx-sheets, xlsx-info | openpyxl | |
| md2pdf | weasyprint, markdown | |
| pdf2md | pdfplumber | |
| md2pptx, pptx2md | python-pptx | |
| msg2md, msg2eml, msg-extract | extract-msg, beautifulsoup4 | |

## PDF System Requirements

WeasyPrint requires system libraries:

- **macOS**: `brew install pango cairo libffi`
- **Ubuntu**: `apt-get install libpango-1.0-0 libpangocairo-1.0-0 libcairo2`
