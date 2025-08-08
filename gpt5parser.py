Below is a targeted, regression-safe set of changes that address your four specific failure modes while keeping the primary goal intact: extract articles, sections, and subsections using DOM signals (headings, tables, styles, and page-breaks). I’ve also added a small regression runner to automatically check for quality regressions between runs.

Summary of what’s fixed

    E-signature noise embedded inside content is removed inline rather than dropping the entire element. This stops DocuSign “Envelope ID” text from polluting section/definition content.
    Page breaks (hr, page-break CSS, Broadridge page break wrappers) are recognized as metadata, and the sentence continuation merger now cleanly merges across them.
    Section heading detection correctly handles “1.Restricted Stock Units” (no space after the period) and table-of-contents (TOC) tables like Broadridge’s. A new TOC table expander extracts Article and Section elements from table-based TOCs.
    We reduced false-positive definitions and improved whitespace handling to avoid weird hierarchies.
    We added a structural de-duplicator that removes duplicated sections/articles produced by multiple detectors (e.g., TOC + headings).
    We included a regression script that compares current vs. baseline metrics and fails the run if orphan rate increases or structural coverage degrades.

What to change in your parser file

    Add a page-break marker and classifier Insert near other MetadataElement classes:

class PageBreakElement(MetadataElement):
metadata_type = "page_break"

Add a new processing step (place with the other steps):

class PageBreakMarkerClassifier(AbstractElementwiseProcessingStep):
"""Marks <hr>, and elements that indicate explicit page-breaks, as metadata.
Also detects common Broadridge PROfile page break wrappers.
"""
PAGE_BREAK_CLASSES = {"pagebreak", "brpfpagebreak", "brpfpagebreakarea"}

def __init__(self) -> None:
    super().__init__(types_to_process={NotYetClassifiedElement, TextElement})

def _process_element(self, element: AbstractSemanticElement, context=None) -> AbstractSemanticElement:
    tag = element.html_tag.get_bs4() if hasattr(element.html_tag, "_bs4") else None
    if not tag:
        return element

    try:
        name = (tag.name or "").lower()
        cls = {c.lower() for c in (tag.get("class") or [])}
        style = (tag.get("style") or "").lower()

        if name == "hr":
            return PageBreakElement(element.html_tag)

        # CSS page-breaks on any block
        if "page-break-before" in style or "page-break-after" in style or "break-before: page" in style or "break-after: page" in style:
            return PageBreakElement(element.html_tag)

        # Broadridge page breaks by class
        if cls.intersection(self.PAGE_BREAK_CLASSES):
            return PageBreakElement(element.html_tag)
    except Exception:
        pass
    return element

2) Strip in-line e-signature and footer noise (without dropping the content)
Add a new step:

class InlineNoiseStripper(AbstractElementwiseProcessingStep):
"""Strip inline e-signature/footer/header noise from text elements while preserving the remaining content."""
_SUB_PATTERNS = [
# DocuSign/HelloSign/PandaDoc noise that appears inline within real content
(r"DocuSign\s+Envelope\s+ID:\s*[A-F0-9-]+(?:\s*[A-F0-9-]+)", ""),  # DocuSign IDs (allow repeated tokens)
(r"HelloSign\s+Signature\s+ID:\s[A-F0-9-]+", ""),
(r"PandaDoc\s+Document\s+ID:\s*[A-F0-9-]+", ""),

    # Common inline footer fragments
    (r"Last\s+Edited:\s*[0-9/:\sAPMapm]+", ""),
    (r"Page\s+\d+\s+of\s+\d+", ""),
    (r"PAGE\s+\d+\b", ""),
    (r"^\s*-\s*\d+\s*-\s*$", ""),
    (r"©\s*\d{4}\s+[A-Z][A-Z\s.&-]{2,}", ""),  # e.g., © 2019 AIR CRE ...

    # Initials/placeholder artifacts
    (r"\*{2,}\s*I\s*-\s*(?:Lessee|Lessor)\d?\s*\*{2,}\s+INITIALS", ""),
    (r"\bINITIALS\b", ""),

    # Redaction stubs
    (r"\[\s*\*{3,}\s*\]", ""),
]

def __init__(self) -> None:
    super().__init__(types_to_process={NotYetClassifiedElement, TextElement})
    self._subs = [(re.compile(pat, re.IGNORECASE), repl) for pat, repl in self._SUB_PATTERNS]

def _process_element(self, element: AbstractSemanticElement, context=None) -> AbstractSemanticElement:
    if not element.html_tag or not getattr(element.html_tag, "text", None):
        return element

    original = element.html_tag.text
    cleaned = original

    for rx, repl in self._subs:
        cleaned = rx.sub(repl, cleaned)

    # normalize spacing
    cleaned = re.sub(r"\s+", " ", cleaned)
    cleaned = re.sub(r"\s+([,.;:)\]])", r"\1", cleaned)
    cleaned = re.sub(r"([(\[])\s+", r"\1", cleaned)
    cleaned = cleaned.strip()

    if cleaned != original:
        new_tag = HtmlTag.clone_with_text(element.html_tag, cleaned)
        # keep the same element type to avoid changing upstream assumptions
        try:
            element.html_tag = new_tag
        except Exception:
            # fallback: return a new TextElement
            return TextElement(new_tag)

    return element

3) Make SmartSectionClassifier and VisualHeadingDetector accept “no-space” headings like “1.Restricted”
Update two regexes.

In SmartSectionClassifierV6._extract_structured_element section_patterns, change the “number” pattern to allow zero whitespace after the dot or closing parenthesis:

    Before:
    (?P<num>\d+(?:.\d+)*)[.)]\s+(?P<title>...)

    After:
    (?P<num>\d+(?:.\d+))[.)]\s(?P<title>...)

Implementation:

Replace the current “number” tuple in section_patterns with this:

(
r"""^
(?P<num>\d+(?:.\d+))
[.)]\s                      # allow zero or more spaces
(?P<title>[A-Z][^a-z]{0,3}|[A-Z][a-z].{0,80})
$""",
"number",
),

In VisualHeadingDetector, update _SECTION_RE similarly:

_SECTION_RE = re.compile(
r"""^(?P<num>\d+(?:.\d+))
[.)]?\s[-–—]?\s*                # allow no space and optional dash
(?P<title>(?!continued\b|page\b|table\b)[^.;]{3,100})$""",
re.IGNORECASE | re.VERBOSE,
)

4. Improve definition extraction to reduce false positives
Tighten LegalContentClassifierV6._extract_definition by removing the overly permissive fallback and adding stronger heuristics.

Replace _extract_definition with:

def _extract_definition(self, text: str) -> tuple[str, str] | None:
"""Definition extraction with reduced false positives.
Prefer quoted terms or TitleCase tokens, require 'means/shall mean/has the meaning'
early in the sentence, and ensure reasonable definition length.
"""
# hard cap inspected length
snippet = text[:600]

patterns = [
    # "Term" means ...
    (r'"([^"]{2,80})"\s+(?:means?|shall\s+mean|has\s+the\s+meaning)\s+(.+)', re.IGNORECASE),
    # the term "Term" means ...
    (r'(?:the\s+)?term\s+"([^"]{2,80})"\s+(?:means?|refers?\s+to|has\s+the\s+meaning)\s+(.+)', re.IGNORECASE),
    # "Term" (defined herein/below/above)
    (r'"([^"]{2,80})"\s*\((?:as\s+)?defined\s+(?:herein|below|above)\)', re.IGNORECASE),
]

for pat, flags in patterns:
    m = re.search(pat, snippet, flags)
    if m:
        term = m.group(1).strip()
        definition = m.group(2).strip() if len(m.groups()) > 1 else text.strip()

        # heuristics: 'means' etc. should be near the start; definition not trivially short
        if len(term) > 1 and len(definition) > 15 and snippet.lower().find("mean") < 120:
            return (term, definition)

# optionally: TitleCase term followed by 'means' very early, but avoid obvious false positives
# Disabled by default to reduce false positives
return None

This removes the broad [A-Z][a-zA-Z\s]+? fallback and avoids “Definition: or the” type misfires.

5. Expand TOC support for table-based TOCs (e.g., Broadridge)
Add a batch step that expands TOC tables into Article and Section elements:

class TOCTableSectionExtractor(AbstractProcessingStep):
"""Expand table-based TOCs into Article and Section elements.
Inserts extracted sections/articles right after the TOC element.
Duplicates will be removed by StructuralDeduplicator.
"""

_ARTICLE_RX = re.compile(r"ARTICLE\s+([IVXLCDM]+|\d+)\s+(.*)$", re.IGNORECASE)
_SECNUM_RX = re.compile(r"^\s*(\d+(?:\.\d+)*)\s*$")

def _process(self, elements: list[AbstractSemanticElement], context=None) -> list[AbstractSemanticElement]:
    out: list[AbstractSemanticElement] = []
    for el in elements:
        out.append(el)
        if isinstance(el, (TableElement, TableOfContentsElement)) and self._is_toc_table(el):
            try:
                table = el.html_tag.get_bs4()
                rows = table.find_all("tr")
            except Exception:
                rows = []

            extracted: list[AbstractSemanticElement] = []
            for tr in rows:
                cells = [c.get_text(" ", strip=True) for c in tr.find_all(["td", "th"])]
                if not cells:
                    continue

                joined = " ".join(cells).strip()

                # Article row like: "ARTICLE I GOVERNANCE MATTERS .... 1"
                am = self._ARTICLE_RX.search(joined)
                if am:
                    num = am.group(1)
                    title = am.group(2).strip().rstrip(".")
                    extracted.append(ArticleElement(el.html_tag, article_number=str(num), article_title=title))
                    continue

                # Section rows often split number/title across cells.
                if len(cells) >= 2 and self._SECNUM_RX.match(cells[0]):
                    secnum = self._SECNUM_RX.match(cells[0]).group(1)
                    title = cells[1].strip().rstrip(".")
                    extracted.append(SectionElement(el.html_tag, section_number=secnum, section_title=title, level=2))
                    continue

                # Fallback: e.g., "1.1 Title .... 5" in a single cell
                m = re.match(r"^\s*(\d+(?:\.\d+)*)[.)]?\s+(.+?)\s+(?:\d+)?\s*$", joined)
                if m:
                    extracted.append(SectionElement(el.html_tag, section_number=m.group(1), section_title=m.group(2).strip(), level=2))

            if extracted:
                out.extend(extracted)

    return out

def _is_toc_table(self, element: AbstractSemanticElement) -> bool:
    try:
        table = element.html_tag.get_bs4()
        txt = table.get_text(" ", strip=True).lower()
        if "table of contents" in txt or "contents" in txt:
            return True
        # Heuristic: many short lines plus some page numbers
        if txt.count(" 1 ") + txt.count(" 2 ") + txt.count(" 3 ") > 4 and ("article" in txt or re.search(r"\b\d+\.\d+\b", txt)):
            return True
    except Exception:
        pass
    return False

6) De-duplicate structure to avoid double sections
Add a de-dup step that drops duplicate sections/articles with same normalized id:

class StructuralDeduplicator(AbstractProcessingStep):
"""Drop duplicate SectionElement/ArticleElement based on normalized_id to avoid double counting (e.g., TOC + body)."""

def _process(self, elements: list[AbstractSemanticElement], context=None) -> list[AbstractSemanticElement]:
    seen_sections: set[str] = set()
    seen_articles: set[str] = set()
    out: list[AbstractSemanticElement] = []

    for el in elements:
        if isinstance(el, SectionElement):
            nid = el.normalized_id()
            if nid and nid in seen_sections:
                continue
            if nid:
                seen_sections.add(nid)
        elif isinstance(el, ArticleElement):
            nid = el.normalized_id()
            if nid and nid in seen_articles:
                continue
            if nid:
                seen_articles.add(nid)
        out.append(el)

    return out

7) Wire the new steps into the pipeline in the right order
Modify AgreementParserv9Enhanced.get_default_steps to add the new steps (comments show where):

def get_default_steps(...):
return [
# Phase 0: Early metadata removal
EarlyMetadataRemoverStep(),
# Phase 0.5: Signature metadata removal
SignatureMetadataRemover(),

    # NEW: Mark page breaks early so mergers can hop across them
    PageBreakMarkerClassifier(),

    # NEW: Strip inline e-signature/footer noise but keep the content
    InlineNoiseStripper(),

    # Phase 1: Initial cleanup
    EmptyElementClassifier(types_to_process={NotYetClassifiedElement}),
    HtmlCommentRemoverStep(),
    RepeatedHeaderFooterDetector(),
    ImprovedMetadataRemoverv8(types_to_process={NotYetClassifiedElement, TextElement}),

    # Phase 3: Title classification
    TitleClassifier(),

    # Early TOC detection
    EnhancedTOCDetector(types_to_process={NotYetClassifiedElement, TextElement, TableElement}),

    # Phase 4: Table processing
    TableClassifier(types_to_process={NotYetClassifiedElement}),
    TableOfContentsClassifier(types_to_process={TableElement}),

    # NEW: Expand table-based TOCs to sections/articles
    TOCTableSectionExtractor(),

    # Phase 5: Table-as-root promotion
    TableRootPromoter(),

    # Phase 6: Structure detection
    SmartSectionClassifierV6(types_to_process={NotYetClassifiedElement, TextElement, TableElement}),
    EnhancedClauseClassifierV6(types_to_process={NotYetClassifiedElement, TextElement}),

    # Fallback title detection
    FallbackTitleClassifier(),
    MainTitleClassifierV6(types_to_process={NotYetClassifiedElement, TextElement}),

    # Legal content
    LegalContentClassifierV6(types_to_process={NotYetClassifiedElement, TextElement}),

    # Phase 10: Text processing
    TextClassifier(types_to_process={NotYetClassifiedElement}),
    VisualHeadingDetector(types_to_process={TextElement}),
    ConsecutivePageNumberClassifier(),
    PageContinuationMerger(),
    HeadingClassifierV6(types_to_process={TextElement}),
    ContentClassifierV6(types_to_process={TextElement}),

    # NEW: drop duplicate sections/articles
    StructuralDeduplicator(),

    # Build hierarchy
    HierarchyBuilder(),

    # Late cleanup + orphan re-attachment
    LateMetadataRemoverStep(),
    OrphanAttacherStep(),

    TextElementMerger(),
]

8) Optional: small robustness improvements

    SmartSectionClassifierV6._process_table_element already tries to read “td” cells; the above TOC support step will add better coverage for classic TOC tables, especially Broadridge.
    VisualHeadingDetector already normalizes whitespace; we further relaxed the section regex as shown.

Additional file: regression_guard.py
This is a simple runner to:

    Build a baseline metrics set (JSON) from all HTML files under a directory.
    Compare a current run vs. baseline and fail with non-zero exit code if:
        orphan_rate increases by more than delta (default 0.5 percentage points), or
        total sections+articles decreases by more than 10% for any individual document.

Save this as regression_guard.py in the same project root:

#!/usr/bin/env python3
import argparse
import json
from pathlib import Path
from collections import defaultdict

from sec_parser.processing_engine.core import AbstractSemanticElementParser
Import your parser

from your_module_file import AgreementParserv9Enhanced, analyze_agreement_v9_enhanced  # adjust import

def run_once(html_dir: Path) -> list[dict]:
results = []
parser = AgreementParserv9Enhanced()
files = sorted(html_dir.glob("*.html"))
for i, f in enumerate(files, 1):
html = f.read_text(errors="ignore")
res = analyze_agreement_v9_enhanced(parser, html, i)
# keep only small metrics we compare
compact = {
"file": f.name,
"orphan_rate": res.get("orphan_rate", 0.0),
"orphan_count": res.get("orphan_count", 0),
"hierarchy_depth": res.get("hierarchy_depth", 0),
"type_counts": res.get("type_counts", {}),
}
results.append(compact)
# fresh parser per file for clean state
parser = AgreementParserv9Enhanced()
return results

def save_baseline(results, outfile: Path):
outfile.write_text(json.dumps(results, indent=2))

def load_baseline(infile: Path):
return json.loads(infile.read_text())

def compare(baseline: list[dict], current: list[dict], max_orphan_delta=0.5, min_struct_ratio=0.9):
failures = []
base_by_file = {r["file"]: r for r in baseline}
cur_by_file = {r["file"]: r for r in current}
files = sorted(set(base_by_file.keys()) & set(cur_by_file.keys()))
for fn in files:
b = base_by_file[fn]
c = cur_by_file[fn]
# orphan rate regression
if c["orphan_rate"] - b["orphan_rate"] > max_orphan_delta:
failures.append((fn, f"Orphan rate regression: {b['orphan_rate']:.2f}% -> {c['orphan_rate']:.2f}%"))
# structure coverage: sections + articles
b_sections = b["type_counts"].get("SectionElement", 0) + b["type_counts"].get("ArticleElement", 0)
c_sections = c["type_counts"].get("SectionElement", 0) + c["type_counts"].get("ArticleElement", 0)
if b_sections > 0:
if c_sections < b_sections * min_struct_ratio:
failures.append((fn, f"Structural coverage decreased: {b_sections} -> {c_sections}"))
return failures

def main():
ap = argparse.ArgumentParser(description="Regression guard for AgreementParserv9Enhanced")
ap.add_argument("--html-dir", default="time_to_get_real/html_files", help="Dir of *.html files")
ap.add_argument("--baseline", required=True, help="Path to baseline.json (for save or compare)")
ap.add_argument("--save-baseline", action="store_true", help="Save new baseline from current run")
ap.add_argument("--max-orphan-delta", type=float, default=0.5, help="Allowed orphan_rate increase (percentage points)")
ap.add_argument("--min-struct-ratio", type=float, default=0.9, help="Allowed ratio of (sections+articles)")
args = ap.parse_args()

html_dir = Path(args.html_dir)
baseline_path = Path(args.baseline)

current = run_once(html_dir)

if args.save_baseline:
    save_baseline(current, baseline_path)
    print(f"Saved baseline to {baseline_path}")
    return

baseline = load_baseline(baseline_path)
failures = compare(baseline, current, args.max_orphan_delta, args.min_struct_ratio)

if failures:
    print("REGRESSION FAILURES:")
    for fn, msg in failures:
        print(f" - {fn}: {msg}")
    raise SystemExit(1)

print("No regressions detected.")

if name == "main":
main()

Usage:

    First, generate a baseline after your improvements are in a good state: python regression_guard.py --html-dir time_to_get_real/html_files --baseline baseline.json --save-baseline
    Later, on a new commit, run: python regression_guard.py --html-dir time_to_get_real/html_files --baseline baseline.json It will exit 1 if regressions are detected.

Why these changes resolve your cases

    Case (1), DocuSign/footers inside content: InlineNoiseStripper removes these inline fragments without losing the rest of the paragraph. This prevents massive cascading false positives like bogus “Definition: or the …”.
    Case (2), page-break continuation: PageBreakMarkerClassifier turns HR and CSS page breaks into metadata. PageContinuationMerger was already designed to merge over metadata; now it correctly merges across page breaks, fixing “4. In the performance … adher[e] to the legal requirements …”.
    Case (3), Broadridge TOC: TOCTableSectionExtractor reads the TOC table and outputs Article/Section elements. Combined with StructuralDeduplicator, this yields useful hierarchy even when the body’s headings are not easily discoverable.
    Case (4), whitespace/no-space headings: Relaxing the “section heading” regexes allows detection of “1.Restricted Stock Units” and similar. Tightening definition extraction reduces “Definition:” noise from colons, lists, and run-ons.

Notes to avoid regressions

    We did not drop small or white-colored elements wholesale to avoid deleting legitimate content (many vendors render the text “invisible” with white text layers). Instead, we remove only known noise patterns inline.
    The dedup step preserves the first occurrence of each article/section id, which keeps your existing correctly detected structure intact while preventing “TOC + body” duplication.
    The regression_guard.py default thresholds are conservative; adjust to your dataset if needed.

If you want me to deliver a fully spliced file with these blocks inserted at the exact locations, paste the latest version of the file path or confirm the module name (your_module_file) so I can provide you with a single ready-to-drop file.
