# AIDLC-business-analyst Changelog

## **1.0.0**

### Major Features

- **BREAKING**: Deprecated slash commands → skill-based auto-invocation with natural language prompts
- **Template-Driven Architecture**: BRD structure dynamically adapts to custom or default templates
- **Dual Generation Modes**: Interview (section-by-section review) or Direct (auto-generate)
- **Interactive BRD Generation**: Section confirmation, revision engine, gap/conflict resolution
- **Agent Modularity**: Separate agents for document-processor, ambiguity-detector, traceability-builder
  - ambiguity-detector: Auto-invoked by ba-brd to detect conflicts, gaps, vague language; reports ambiguities pre-generation
  - traceability-builder: Auto-invoked by ba-brd when template contains traceability sections to fetch Rally hierarchy and build Epic→Capability→Feature matrix
- **Content Conciseness**: 40-50% verbosity reduction (tables over prose, compact notation)
- **Template Selection Workflow**: Prompts for custom template before using predefined (Legacy Inscore, SAFE BRD)

### Architecture Changes

- **Replaced Commands With Skills**: Removed commands folder, added 4 skills (ba-brd, ba-generate, ba-explore, ba-validate)
- **Document Processor Agent**: Multi-format ingestion (DOCX, MD, PDF, TXT, MSG) with paragraph-level BRD section mapping
- **Interactive Workflow**: Section-by-section confirmation with revision engine
  - gap-resolver skill: Interactive gap filling with targeted questions
  - conflict-resolver skill: Ambiguity detection and resolution
- **Mode Selection**: Process documents first, then ask user to choose Interview or Direct mode
  - Shortcuts: "interactive BRD" or "--direct" keywords skip the question
  - Backward compatible with existing keywords

### ba-brd Skill Workflow

```
┌─────────────────────────────────────────────────────────────────────┐
│                    ba-brd Generation Workflow                       │
└─────────────────────────────────────────────────────────────────────┘

Phase 0: Template Loading
    │
    ├──> brd-template skill
    │       └──> Template selection (custom/SAFE/legacy)
    │
    v
Phase 1: Document Processing
    │
    ├──> document-processor agent
    │       └──> Extract paragraphs, map to BRD sections, save JSON
    │
    v
Phase 2: Output Path Discovery
    │
    ├──> 3-tier approach (metadata → patterns → user prompt)
    │
    v
Phase 2.5: Generation Mode Selection
    │
    ├──> AskUserQuestion: Interview vs Direct mode
    │
    v
Phase 3: BRD Content Generation
    │
    ├──> If Interview Mode:
    │       └──> Section-by-section review with AskUserQuestion
    │           └──> Options: Approve / Modify / Defer as gap
    │
    └──> If Direct Mode:
            └──> Auto-generate all sections without review
    │
    v
Phase 3a: Ambiguity Detection (Mandatory)
    │
    ├──> ambiguity-detector agent
    │       └──> Detect conflicts, gaps, vague language
    │
    v
Phase 4: Traceability Collection (Template-Conditional)
    │
    ├──> traceability-builder agent (if template includes traceability)
    │       └──> Rally hierarchy + source citations
    │
    v
Phase 5: Final BRD Assembly
    │
    ├──> Write BRD.md
    ├──> Convert to BRD.docx (documents skill)
    │
    v
Phase 6: Optional Gap/Conflict Resolution
    │
    ├──> AskUserQuestion: Resolve now or skip?
    │
    └──> If resolve:
            ├──> gap-resolver skill (for missing content)
            ├──> conflict-resolver skill (for contradictions)
            └──> Update BRD.md and BRD.docx with resolutions
```

### Usage Examples

```bash
# Natural language prompts (new approach)
Generate a BRD from ./requirements
Create an EPIC from ./vision-docs
Extract business rules from ./specifications

# Interview mode (section-by-section review)
Generate BRD from ./docs
# → Plugin shows each section for approval/modification

# Direct mode (fast auto-generation)
Generate BRD from ./docs --direct
# → Plugin generates all sections without review

# Custom template with Rally integration
Generate BRD from ./business-requirements --epic E78901
# → Uses custom template + fetches Rally hierarchy

# Validation and ambiguity detection
Check the BRD at ./output/BRD.md for ambiguities
Validate the BRD at ./docs/requirements against source documents
```

---

## **1.4.0** - 2026.PI1.Iteration1 (2026-01-22)

**Sprint Period**: January 8 - January 22, 2026

### Business Requirements Management (F116884: BRD Generation from Documents)

- **US774989**: Implement /AIDLC-business-analyst:brd command - Document-to-BRD generation supporting Word, PDF, markdown inputs,
  .docx output using template format, graceful handling of missing information

---

## 2025/2026IterationIP v1.3.0 (2026-01-09)

### Performance Optimizations

- **US774971**: Optimize AIDLC-business-analyst Plugin for Token Efficiency and Speed - Split Monolithic Files, Remove Duplication, On-Demand Loading, Haiku Routing, Parallel Execution
