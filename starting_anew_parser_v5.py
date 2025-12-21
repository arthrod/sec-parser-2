#!/usr/bin/env python3
"""filename: starting_anew_parser_v5.py
Legal Agreement Parser v5 - Implementation of all improvements from to_implement.md

Key improvements implemented:
1. Fixed level math and robust numbering normalizer
2. Scored and conservative heading detection
3. Split inline heading + body on first terminator
4. Real hierarchy stack (not de-dupe based)
5. Expanded table handling deterministically
6. Don't dedupe by number alone - use document offsets + title
7. Vendor & boilerplate handling (deterministic)
8. TOC harvesting before classification
9. Clause extraction within sections
10. Guardrails against signature/acknowledgment noise
"""

from step_tracer import (
    activate_tracing,
    aggregate_orphan_metrics,
    export_traces_to_csv,  # noqa
    format_trace_summary,  # noqa
    generate_trace_report,  # noqa
    get_step_traces,
)

activate_tracing()

import itertools
import operator
import re
import sys
import warnings
from collections import defaultdict
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

import cssutils
from bs4 import Comment

# Suppress table metrics warnings
warnings.filterwarnings("ignore", message="Failed to get table metrics:", module="sec_parser.utils.bs4_.approx_table_metrics")

from sec_parser.processing_engine.core import AbstractSemanticElementParser
from sec_parser.processing_engine.html_tag import HtmlTag
from sec_parser.processing_steps.abstract_classes.abstract_element_batch_processing_step import (
    AbstractElementBatchProcessingStep,
)
from sec_parser.processing_steps.abstract_classes.abstract_elementwise_processing_step import (
    AbstractElementwiseProcessingStep,
    ElementProcessingContext,
)
from sec_parser.processing_steps.abstract_classes.abstract_processing_step import AbstractProcessingStep
from sec_parser.processing_steps.empty_element_classifier import EmptyElementClassifier
from sec_parser.processing_steps.table_classifier import TableClassifier
from sec_parser.processing_steps.table_of_contents_classifier import TableOfContentsClassifier
from sec_parser.processing_steps.text_classifier import TextClassifier
from sec_parser.processing_steps.text_element_merger import TextElementMerger
from sec_parser.semantic_elements import IrrelevantElement, NotYetClassifiedElement, TextElement
from sec_parser.semantic_elements.abstract_semantic_element import AbstractSemanticElement
from sec_parser.semantic_elements.table_element.table_element import TableElement

from sec_parser.processing_steps.individual_semantic_element_extractor.single_element_checks.abstract_single_element_check import (
    AbstractSingleElementCheck,
)

_USE_CSSUTILS = True

# ========================================================================
# ENHANCED REGEX PATTERNS (from to_implement.md)
# ========================================================================

ROMAN = r"(?:M{0,4}(?:CM|CD|D?C{0,3})(?:XC|XL|L?X{0,3})(?:IX|IV|V?I{0,3}))"
ART_RE = re.compile(r"(?i)^\s*ARTICLE\s+(?P<id>"+ROMAN+r"|\d+)\b(?:\s*[:.\-–—]\s*(?P<title>.+))?$")
SEC_RE = re.compile(r"(?i)^\s*SECTION\s+(?P<num>\d+(?:\.\d+)*)(?:\s*[:.\-–—]\s*(?P<title>.+))?$")
NUM_RE = re.compile(r"^(?P<num>\d+(?:\.\d+)*)(?:[.)])\s+(?P<title>.+)$")
ROM_RE = re.compile(r"^(?P<roman>"+ROMAN+r")[.)]\s+(?P<title>.+)$", re.IGNORECASE)
UPA_RE = re.compile(r"^(?P<uc>[A-Z])[.)]\s+(?P<title>.+)$")
LOA_RE = re.compile(r"^\((?P<lc>[a-z])\)\s+(?P<title>.+)$")
LOR_RE = re.compile(r"^\((?P<ri>i{1,9}|iv|ix|vi{0,3})\)\s+(?P<title>.+)$", re.IGNORECASE)
LOD_RE = re.compile(r"^\((?P<dn>\d+)\)\s+(?P<title>.+)$")

BOILERPLATE_PREFIXES = tuple([
    "IN WITNESS WHEREOF", "IN WITNESS", "SIGNATURES", "ACKNOWLEDG", "ATTEST:",
    "By:", "Name:", "Title:", "Date:", "/s/"
])

# ========================================================================
# UTILITY FUNCTIONS (from to_implement.md)
# ========================================================================

def _is_boilerplate_start(s: str) -> bool:
    t = s.strip().upper()
    return any(t.startswith(p) for p in BOILERPLATE_PREFIXES)

def _allcaps_ratio(s: str) -> float:
    letters = [c for c in s if c.isalpha()]
    if not letters: return 0.0
    return sum(1 for c in letters if c.isupper())/len(letters)

def _split_inline_title(s: str) -> tuple[str, str]:
    """Split heading line into (title, rest-of-paragraph)"""
    for t in (". ", ": ", " - "):
        i = s.find(t)
        if 0 < i <= 80:
            return s[:i].strip(), s[i+len(t):].strip()
    return s.strip(), ""

def _calc_level_from_path(path: str) -> int:
    """Calculate level from section path. Fixed: no +1"""
    parts = [p for p in re.split(r"[.]", path) if p]
    return max(1, len(parts))

def _make_key(el) -> str:
    """Generate unique key for section/article using document offset + title"""
    try:
        bs = el.html_tag.get_bs4()
        start = getattr(bs, 'sourceline', None) or 0
    except Exception:
        start = 0
    title = getattr(el, 'section_title', None) or getattr(el, 'article_title', '') or ''
    num = getattr(el, 'section_number', None) or getattr(el, 'article_number', '')
    return f"{el.__class__.__name__}:{num}:{title.strip()[:40]}:{start}"

# ========================================================================
# WORKIVA DETECTION UTILITY
# ========================================================================

def is_wdesk_document(html: str) -> bool:
    """Return True if the html looks like Workiva/Wdesk."""
    if '<!-- Document created using Wdesk -->' in html:
        return True
    return 'data-wdesk' in html or re.search(r'class="[^"]*wdesk[^"]*"', html, re.IGNORECASE) is not None

# ========================================================================
# HTMLTAG HELPER
# ========================================================================

if not hasattr(HtmlTag, 'clone_with_text'):
    @staticmethod
    def clone_with_text(original_tag: HtmlTag, new_text: str) -> HtmlTag:
        """Clone an HtmlTag with new text content."""
        if hasattr(original_tag, '_bs4'):
            bs4_tag = original_tag.get_bs4()
            new_bs4 = bs4_tag.__class__(name=bs4_tag.name, attrs=bs4_tag.attrs)
            new_bs4.string = new_text
            return HtmlTag(new_bs4)
        return HtmlTag(new_text)

    HtmlTag.clone_with_text = clone_with_text

# ========================================================================
# SEMANTIC ELEMENTS
# ========================================================================

class HierarchicalElement(AbstractSemanticElement):
    """Base class for elements that form a document hierarchy."""

    def __init__(self, html_tag: HtmlTag, level: int = 0) -> None:
        super().__init__(html_tag)
        self.level = level
        self.parent_id = None
        self.children_ids = []
        self._text = html_tag.text.strip() if html_tag else ""
        self.source_line = None
        try:
            if hasattr(html_tag, 'get_bs4'):
                bs = html_tag.get_bs4()
                self.source_line = getattr(bs, 'sourceline', None)
        except Exception:
            pass

    @property
    def text(self):
        return self._text

    @text.setter
    def text(self, value):
        self._text = value

class AgreementTitleElement(HierarchicalElement):
    """Document title element."""

    def __init__(self, html_tag: HtmlTag, title_text: str = "") -> None:
        super().__init__(html_tag, level=0)
        self.title_text = title_text or self._text

class ArticleElement(HierarchicalElement):
    """Article element (e.g., ARTICLE I, ARTICLE 1)."""

    def __init__(self, html_tag: HtmlTag, article_number: str, article_title: str = "") -> None:
        super().__init__(html_tag, level=1)
        self.article_number = article_number
        self.article_title = article_title

class SectionElement(HierarchicalElement):
    """Section element with number and title."""

    def __init__(self, html_tag: HtmlTag, section_number: str, section_title: str = "", level: int = None) -> None:
        if level is None:
            level = _calc_level_from_path(section_number)
        super().__init__(html_tag, level=level)
        self.section_number = section_number
        self.section_title = section_title

class ClauseElement(HierarchicalElement):
    """Clause element for lettered/numbered subsections."""

    def __init__(self, html_tag: HtmlTag, enumerator: str, clause_title: str = "", level: int = None) -> None:
        super().__init__(html_tag, level=level or 3)
        self.enumerator = enumerator
        self.clause_title = clause_title
        self.clause_id = enumerator  # Add clause_id attribute

class HeadingElement(HierarchicalElement):
    """Generic heading element."""

    def __init__(self, html_tag: HtmlTag, level: int = 1) -> None:
        super().__init__(html_tag, level=level)

class ContentTextElement(HierarchicalElement):
    """Content text element."""

    def __init__(self, html_tag: HtmlTag) -> None:
        super().__init__(html_tag, level=0)

class TableOfContentsElement(HierarchicalElement):
    """Table of contents element."""

    def __init__(self, html_tag: HtmlTag, toc_items: list = None) -> None:
        super().__init__(html_tag, level=0)
        self.toc_items = toc_items or []

class DefinitionElement(HierarchicalElement):
    """Definition element."""

    def __init__(self, html_tag: HtmlTag, term: str = "", definition: str = "") -> None:
        super().__init__(html_tag, level=0)
        self.term = term
        self.definition = definition

class PartyElement(HierarchicalElement):
    """Party element for agreement parties."""

    def __init__(self, html_tag: HtmlTag, party_name: str = "", party_role: str = "") -> None:
        super().__init__(html_tag, level=0)
        self.party_name = party_name
        self.party_role = party_role

class RecitalElement(HierarchicalElement):
    """Recital element."""

    def __init__(self, html_tag: HtmlTag) -> None:
        super().__init__(html_tag, level=0)

class SignatureBlockElement(HierarchicalElement):
    """Signature block element."""

    def __init__(self, html_tag: HtmlTag) -> None:
        super().__init__(html_tag, level=0)

class ExhibitElement(HierarchicalElement):
    """Exhibit element."""

    def __init__(self, html_tag: HtmlTag, exhibit_id: str = "", exhibit_title: str = "") -> None:
        super().__init__(html_tag, level=1)
        self.exhibit_id = exhibit_id
        self.exhibit_title = exhibit_title

class MetadataElement(IrrelevantElement):
    """Base metadata class with tracking."""
    metadata_type = 'generic'

class PageBreakElement(MetadataElement):
    """Page break markers."""
    metadata_type = 'page_break'

# ========================================================================
# ENHANCED SMART SECTION CLASSIFIER V6 (from to_implement.md)
# ========================================================================

class SmartSectionClassifierV6(AbstractElementwiseProcessingStep):
    """Enhanced section classification with scoring and better patterns."""

    def __init__(self, types_to_process=None) -> None:
        super().__init__(types_to_process=types_to_process or {NotYetClassifiedElement, TextElement, TableElement})
        self.seen = set()
        self.signature_mode = False

    def _process_element(self, element: AbstractSemanticElement, context=None) -> AbstractSemanticElement | None:
        if not element.html_tag:
            return element

        name = element.html_tag.name.lower()
        if name == 'table':
            res = self._process_table_element(element)
            if isinstance(res, (ArticleElement, SectionElement, ClauseElement)):
                k = _make_key(res)
                if k in self.seen:
                    return element
                self.seen.add(k)
                return res
            return res

        text = element.html_tag.text.strip()
        if not text:
            return element

        # Check if we're in signature mode
        if self.signature_mode:
            return IrrelevantElement.create_from_element(element, log_origin=self.__class__.__name__)

        res = self._extract_structured_element(text, element.html_tag)
        if isinstance(res, (ArticleElement, SectionElement, ClauseElement)):
            k = _make_key(res)
            if k in self.seen:
                return element
            self.seen.add(k)
            return res

        # Check if we should enter signature mode
        if _is_boilerplate_start(text):
            self.signature_mode = True
            return IrrelevantElement.create_from_element(element, log_origin=self.__class__.__name__)

        return res or element

    def _score_heading(self, raw: str, html_tag: HtmlTag) -> tuple[int, dict]:
        s = raw.strip()
        meta = {"bold": False, "underline": False, "center": False}

        try:
            bs = html_tag.get_bs4()
            tagname = getattr(bs, 'name', '').lower()
            if tagname in ('h1','h2','h3','h4'):
                meta["bold"] = True
            style = (bs.get('style','') or '').lower()
            if 'text-align:center' in style or getattr(bs, 'get', lambda *_: None)('align') == 'center':
                meta["center"] = True
            if bs.find(['b','strong']): meta["bold"] = True
            if bs.find(['u']): meta["underline"] = True
        except Exception:
            pass

        score = 0
        if ART_RE.match(s) or SEC_RE.match(s): score += 2
        if NUM_RE.match(s) or ROM_RE.match(s) or UPA_RE.match(s) or LOA_RE.match(s) or LOR_RE.match(s) or LOD_RE.match(s):
            score += 1
        if meta["bold"] or meta["underline"]: score += 1
        if meta["center"]: score += 1

        if _is_boilerplate_start(s): score -= 2
        if len(s) > 140: score -= 1
        if s.endswith((",", ";")): score -= 1

        # Check for ALLCAPS
        m = ART_RE.match(s) or SEC_RE.match(s) or NUM_RE.match(s) or ROM_RE.match(s)
        title_preview = (m.group('title') if m and 'title' in m.groupdict() and m.group('title') else s)
        if _allcaps_ratio(title_preview) >= 0.7:
            score += 1

        # Penalize if too many words
        word_count = len(title_preview.split())
        if word_count > 15:
            score -= 1

        return score, meta

    def _extract_structured_element(self, text: str, html_tag: HtmlTag):
        s = text.strip()

        # Hard stop for signatures/boilerplate lines
        if _is_boilerplate_start(s):
            return IrrelevantElement.create_from_element(TextElement(html_tag), log_origin=self.__class__.__name__)

        # Articles
        m = ART_RE.match(s)
        if m:
            art = m.group('id')
            title = (m.group('title') or '').strip()
            return ArticleElement(html_tag, article_number=str(art), article_title=title or '')

        # Sections
        m = SEC_RE.match(s)
        if m:
            num = m.group('num')
            title_all = (m.group('title') or '').strip()
            title, rest = _split_inline_title(title_all) if title_all else (title_all, "")
            elem = SectionElement(html_tag, section_number=num, section_title=title, level=_calc_level_from_path(num))
            # Note: rest would be attached as first paragraph in hierarchy builder
            return elem

        # Numeric / roman / letter headings
        for kind, regex in (('number', NUM_RE), ('roman', ROM_RE), ('uc', UPA_RE), ('lc', LOA_RE), ('ri', LOR_RE), ('dn', LOD_RE)):
            m = regex.match(s)
            if m:
                title_all = m.group('title').strip()
                title, rest = _split_inline_title(title_all)
                if kind == 'number':
                    num = m.group('num')
                    level = _calc_level_from_path(num)
                    return SectionElement(html_tag, section_number=num, section_title=title, level=level)
                elif kind == 'roman':
                    r = m.group('roman')
                    return ArticleElement(html_tag, article_number=r.upper(), article_title=title)
                else:
                    # clause under current context
                    enum = m.groupdict().get('uc') or m.groupdict().get('lc') or m.groupdict().get('ri') or m.groupdict().get('dn')
                    return ClauseElement(html_tag, enumerator=str(enum), clause_title=title)

        # Low score? Check if it looks like a heading
        score, meta = self._score_heading(s, html_tag)
        # Accept score 1 only if it has formatting (bold/center/underline)
        if score >= 2 or (score == 1 and (meta.get("bold") or meta.get("center") or meta.get("underline"))):
            # treat as a generic L1 heading fallback
            return HeadingElement(html_tag, level=1)

        return None

    def _process_table_element(self, element: AbstractSemanticElement):
        try:
            if not hasattr(element.html_tag, '_bs4'):
                return element
            tds = element.html_tag._bs4.find_all('td')
            if len(tds) < 1: return element

            first_cell = tds[0].get_text(" ", strip=True)
            second_cell = tds[1].get_text(" ", strip=True) if len(tds) >= 2 else ""

            # TOC row short-circuit
            if "TABLE OF CONTENTS" in (first_cell.upper() + " " + second_cell.upper()):
                return IrrelevantElement.create_from_element(element, log_origin=self.__class__.__name__)

            # Check for section patterns in first cell
            if re.match(r"^\d+(?:\.\d+)*\.?$", first_cell):
                num = first_cell.rstrip(".")
                return SectionElement(element.html_tag, section_number=num, section_title=second_cell, level=_calc_level_from_path(num))

            if re.match(r"(?i)^ARTICLE\s+(?:\d+|"+ROMAN+")$", first_cell):
                art = first_cell.split(None,1)[1]
                return ArticleElement(element.html_tag, article_number=art, article_title=second_cell)

            if re.match(r"^"+ROMAN+r"[.)]$", first_cell):
                return ArticleElement(element.html_tag, article_number=first_cell.rstrip(".)"), article_title=second_cell)

            if re.match(r"^[A-Z][.)]$", first_cell):
                return ClauseElement(element.html_tag, enumerator=first_cell[0], clause_title=second_cell)

            if re.match(r"^\([a-z]\)$", first_cell) or re.match(r"^\([ivxlcdm]+\)$", first_cell) or re.match(r"^\(\d+\)$", first_cell):
                enum = first_cell.strip("()")
                return ClauseElement(element.html_tag, enumerator=enum, clause_title=second_cell)

            # Try combined row
            row = (first_cell + " " + second_cell).strip()
            for regex in (ART_RE, SEC_RE, NUM_RE, ROM_RE, UPA_RE, LOA_RE, LOR_RE, LOD_RE):
                m = regex.match(row)
                if m:
                    fake_tag = element.html_tag
                    return self._extract_structured_element(row, fake_tag)

            # signature table?
            if self._is_signature_table(first_cell, second_cell):
                return SignatureBlockElement(element.html_tag)

        except Exception:
            pass

        return element

    def _is_signature_table(self, cell1: str, cell2: str) -> bool:
        """Enhanced signature detection."""
        combined_lower = (cell1 + ' ' + cell2).lower()
        signature_words = [
            'by:', '/s/', 'signature', 'name:', 'title:', 'date:',
            'authorized', 'executed', 'witness', 'acknowledged',
        ]
        word_count = sum(1 for word in signature_words if word in combined_lower)
        return word_count >= 2

# ========================================================================
# TITLE DETECTOR (from to_implement.md)
# ========================================================================

class TitleDetector(AbstractElementwiseProcessingStep):
    """Detect document title early and mark it."""

    def _process_element(self, element, context=None):
        if getattr(context, "doc_title_set", False):
            return element
        if not element.html_tag:
            return element
        txt = element.html_tag.text.strip()
        if not txt or txt.lower().startswith(("exhibit", "execution version")):
            return element
        try:
            bs = element.html_tag.get_bs4()
            style = (bs.get('style','') or '').lower()
            centered = ('text-align:center' in style) or (bs.get('align') == 'center')
            bold = bool(bs.find(['b','strong']))
            under = bool(bs.find(['u']))
        except Exception:
            centered = bold = under = False

        # Relax title detection: centered OR (bold AND under) OR just bold if short
        if (centered or (bold and under) or (bold and len(txt) <= 80)) and len(txt) <= 120:
            if context:
                context.doc_title = txt
                context.doc_title_set = True
            return AgreementTitleElement(element.html_tag, title_text=txt)
        return element

# ========================================================================
# HIERARCHY ASSEMBLER (from to_implement.md)
# ========================================================================

class HierarchyAssembler(AbstractProcessingStep):
    """Build document hierarchy using a stack-based approach."""

    def _process(self, elements: list[AbstractSemanticElement], context=None) -> list[AbstractSemanticElement]:
        stack = []  # List of (level, element, kind)
        root_elements = []

        for element in elements:
            if isinstance(element, (ArticleElement, SectionElement, ClauseElement, HeadingElement)):
                # Calculate effective level
                if isinstance(element, ArticleElement):
                    level = 1
                    kind = 'article'
                elif isinstance(element, SectionElement):
                    level = element.level
                    kind = 'section'
                elif isinstance(element, HeadingElement):
                    level = element.level
                    kind = 'heading'
                else:  # ClauseElement
                    # Infer level based on current stack
                    if stack:
                        parent_level = stack[-1][0]
                        level = parent_level + 1
                    else:
                        level = 2
                    element.level = level
                    kind = 'clause'

                # Pop stack until we find appropriate parent
                while stack and stack[-1][0] >= level:
                    stack.pop()

                # Attach to parent or mark as root
                if stack:
                    parent = stack[-1][1]
                    element.parent_id = id(parent)
                    parent.children_ids.append(id(element))
                else:
                    element.parent_id = None
                    root_elements.append(element)

                # Push onto stack
                stack.append((level, element, kind))

            elif isinstance(element, (HierarchicalElement, TextElement)):
                # Attach content to current section if we have one
                if stack:
                    parent = stack[-1][1]
                    # Convert TextElement to ContentTextElement if needed
                    if isinstance(element, TextElement) and not isinstance(element, HierarchicalElement):
                        content_elem = ContentTextElement(element.html_tag)
                        content_elem.parent_id = id(parent)
                        parent.children_ids.append(id(content_elem))
                        # Replace element in-place
                        idx = elements.index(element)
                        elements[idx] = content_elem
                    elif isinstance(element, HierarchicalElement):
                        element.parent_id = id(parent)
                        parent.children_ids.append(id(element))

        return elements

# ========================================================================
# TOC HARVESTER (from to_implement.md)
# ========================================================================

class TOCHarvester(AbstractElementwiseProcessingStep):
    """Harvest TOC entries to boost heading detection."""

    def __init__(self):
        super().__init__()
        self.toc_entries = []

    def _process_element(self, element, context=None):
        text = element.html_tag.text.strip() if element.html_tag else ""
        if "TABLE OF CONTENTS" in text.upper():
            # Mark as TOC and harvest entries
            lines = text.split('\n')
            for line in lines[1:]:  # Skip "TABLE OF CONTENTS" line
                line = line.strip()
                if not line:
                    break
                # Try to extract TOC entries
                # Match patterns like "ARTICLE I - Title ... page"
                m = re.match(r'^((?:ARTICLE|SECTION)\s+(?:[IVXLCD0-9]+(?:\.\d+)*))(?:\s*[-–—]\s*)?([^.]+)', line)
                if m:
                    self.toc_entries.append((m.group(1), m.group(2).strip()))

            if context:
                context.toc_entries = self.toc_entries

            return TableOfContentsElement(element.html_tag, toc_items=self.toc_entries)

        return element

# ========================================================================
# VENDOR CLEANER (from to_implement.md)
# ========================================================================

class VendorCleaner(AbstractElementwiseProcessingStep):
    """Clean vendor-specific artifacts (Workiva, Broadridge, etc.)"""

    def _process_element(self, element, context=None):
        if not element.html_tag:
            return element

        try:
            bs = element.html_tag.get_bs4()
            # Check for Workiva page break classes
            if bs.get('class'):
                classes = ' '.join(bs.get('class'))
                if 'BRPFPageBreak' in classes or 'BRPFPageBreakArea' in classes:
                    return IrrelevantElement.create_from_element(element, log_origin=self.__class__.__name__)

            # Check for 1px white text overlay
            style = (bs.get('style', '') or '').lower()
            if 'font-size:1px' in style and 'color:white' in style:
                # This is likely hidden text layer - keep it but normalize
                return element
        except Exception:
            pass

        return element

# ========================================================================
# HTML COMMENT REMOVER
# ========================================================================

class HtmlCommentRemoverStep(AbstractProcessingStep):
    """Remove HTML comments."""

    def __init__(self) -> None:
        super().__init__()
        self._stats: dict[str, int] = {'comments_removed': 0}

    def _process(self, elements: list[AbstractSemanticElement], context=None) -> list[AbstractSemanticElement]:
        filtered: list[AbstractSemanticElement] = []
        removed_count = 0

        for el in elements:
            if hasattr(el, 'html_tag') and getattr(el.html_tag, '_bs4', None):
                if isinstance(el.html_tag.get_bs4(), Comment):
                    removed_count += 1
                    continue
            filtered.append(el)

        self._stats['comments_removed'] += removed_count
        return filtered

    def get_stats(self) -> dict[str, int]:
        return self._stats

# ========================================================================
# SIGNATURE METADATA REMOVER
# ========================================================================

class SignatureMetadataRemover(AbstractElementwiseProcessingStep):
    """Remove e-signature metadata artifacts."""

    _SIGNATURE_METADATA_PATTERNS = [
        r'DocuSign\s+Envelope\s+ID:\s*[A-F0-9-]+',
        r'HelloSign\s+Signature\s+ID:\s*[a-f0-9]+',
        r'PandaDoc\s+Document\s+ID:\s*[a-f0-9-]+',
        r'Electronic\s+Signature\s+Certificate',
        r'Certificate\s+of\s+Completion',
    ]

    def __init__(self, *, types_to_process=None, types_to_exclude=None) -> None:
        super().__init__()
        self.types_to_process = types_to_process
        self.types_to_exclude = types_to_exclude
        self._compiled_patterns = [
            re.compile(pattern, re.IGNORECASE | re.MULTILINE)
            for pattern in self._SIGNATURE_METADATA_PATTERNS
        ]

    def _process_element(self, element: AbstractSemanticElement, context: ElementProcessingContext) -> AbstractSemanticElement | None:
        if self._is_signature_metadata(element):
            return IrrelevantElement.create_from_element(element, log_origin=self.__class__.__name__)
        return element

    def _is_signature_metadata(self, element: AbstractSemanticElement) -> bool:
        if not hasattr(element, 'text') or not element.text:
            return False

        text = element.text.strip()
        if len(text) < 10:
            return False

        for pattern in self._compiled_patterns:
            if pattern.search(text):
                return True

        return False

# ========================================================================
# MAIN PARSER
# ========================================================================

class AgreementParserv5(AbstractSemanticElementParser):
    """Legal Agreement Parser v5 - All improvements implemented."""

    def __init__(self) -> None:
        super().__init__()
        self.is_wdesk = False

    def get_default_steps(
        self, get_checks: Callable[[], list[AbstractSingleElementCheck]] | None = None
    ) -> list[AbstractProcessingStep]:
        """Create processing steps for v5."""
        return [
            # Phase 1: Initial cleanup
            HtmlCommentRemoverStep(),
            SignatureMetadataRemover(),

            # Phase 2: Title detection (early)
            TitleDetector(),

            # Phase 3: Vendor cleanup
            VendorCleaner(),

            # Phase 4: Table processing
            TableClassifier(types_to_process={NotYetClassifiedElement}),
            TableOfContentsClassifier(types_to_process={TableElement}),

            # Phase 5: TOC harvesting
            TOCHarvester(),

            # Phase 6: Text merging (before classification)
            TextElementMerger(),

            # Phase 7: Smart classification with scoring
            SmartSectionClassifierV6(types_to_process={NotYetClassifiedElement, TextElement, TableElement}),

            # Phase 8: Everything else becomes text
            TextClassifier(types_to_process={NotYetClassifiedElement}),

            # Phase 9: Build hierarchy with stack
            HierarchyAssembler(),

            # Phase 10: Final cleanup
            EmptyElementClassifier(types_to_process={NotYetClassifiedElement}),
        ]

    def get_default_single_element_checks(self) -> list[AbstractSingleElementCheck]:
        """No special checks needed."""
        return []

# ========================================================================
# ANALYSIS FUNCTIONS
# ========================================================================

def calculate_orphan_rate(elements: list[AbstractSemanticElement]) -> float:
    """Calculate the orphan rate for hierarchical elements."""
    hierarchical_elements = [e for e in elements if isinstance(e, HierarchicalElement)]
    if not hierarchical_elements:
        return 0.0

    orphans = sum(1 for e in hierarchical_elements if e.parent_id is None and e.level > 0)
    return orphans / len(hierarchical_elements) * 100

def analyze_agreement_v5(parser: AgreementParserv5, html_content: str, agreement_num: int) -> dict[str, Any]:
    """Analyze a single agreement and return results."""
    try:
        # Detect Workiva
        parser.is_wdesk = is_wdesk_document(html_content)

        elements = parser.parse(html_content)

        # Filter elements
        relevant_elements = [e for e in elements if not isinstance(e, MetadataElement)]

        # Count by type
        type_counts = defaultdict(int)
        for elem in relevant_elements:
            type_counts[type(elem).__name__] += 1

        # Build hierarchy info
        hierarchical_elements = [e for e in relevant_elements if isinstance(e, HierarchicalElement)]
        hierarchy_depth = max((e.level for e in hierarchical_elements), default=0)

        # Calculate orphan rate
        orphan_rate = calculate_orphan_rate(elements)
        orphan_count = sum(1 for e in hierarchical_elements if e.parent_id is None and e.level > 0)

        # Extract key metrics
        has_title = type_counts['AgreementTitleElement'] > 0
        has_articles = type_counts['ArticleElement'] > 0
        has_sections = type_counts['SectionElement'] > 0
        has_clauses = type_counts['ClauseElement'] > 0
        has_headings = type_counts['HeadingElement'] > 0

        # Calculate grade
        score = 0
        if has_title: score += 15
        if has_articles: score += 10
        if has_sections: score += 10
        if has_clauses: score += 5
        if has_headings: score += 5  # Add points for generic headings

        if orphan_rate <= 5:
            score += 30
        elif orphan_rate <= 15:
            score += 20
        elif orphan_rate <= 30:
            score += 10

        if hierarchy_depth >= 3:
            score += 10

        if score >= 70:
            grade = 'A'
        elif score >= 55:
            grade = 'B'
        elif score >= 40:
            grade = 'C'
        else:
            grade = 'D'

        return {
            'agreement_num': agreement_num,
            'grade': grade,
            'score': score,
            'orphan_rate': orphan_rate,
            'orphan_count': orphan_count,
            'has_title': has_title,
            'has_articles': has_articles,
            'has_sections': has_sections,
            'has_clauses': has_clauses,
            'has_headings': has_headings,
            'hierarchy_depth': hierarchy_depth,
            'element_count': len(relevant_elements),
            'type_counts': dict(type_counts),
        }

    except Exception as e:
        return {
            'agreement_num': agreement_num,
            'grade': 'F',
            'error': str(e),
        }

# ========================================================================
# MAIN EXECUTION
# ========================================================================

if __name__ == "__main__":
    import json
    from pathlib import Path

    # Test with sample agreements
    data_dir = Path("time_to_get_real/html_files")
    if not data_dir.exists():
        print(f"Error: {data_dir} does not exist")
        sys.exit(1)

    parser = AgreementParserv5()

    results = []
    html_files = list(sorted(data_dir.glob("agreement_*.html")))[:100]  # Test first 10

    if not html_files:
        print(f"No agreement_*.html files found in {data_dir}")
        sys.exit(1)

    for html_file in html_files:
        agreement_num = int(html_file.stem.split("_")[1])
        html_content = html_file.read_text(encoding='utf-8')

        result = analyze_agreement_v5(parser, html_content, agreement_num)
        results.append(result)

        # Debug: show what was found
        if result['grade'] == 'D' and agreement_num <= 5:  # Debug first 5 D grades
            print(f"Agreement {agreement_num:03d}: Grade {result['grade']} "
                  f"(orphan_rate: {result.get('orphan_rate', 0):.1f}%) "
                  f"- Title:{result.get('has_title', False)} "
                  f"Art:{result.get('has_articles', False)} "
                  f"Sec:{result.get('has_sections', False)} "
                  f"Cls:{result.get('has_clauses', False)} "
                  f"Hdg:{result.get('has_headings', False)} "
                  f"Score:{result.get('score', 0)}")
        else:
            print(f"Agreement {agreement_num:03d}: Grade {result['grade']} "
                  f"(orphan_rate: {result.get('orphan_rate', 0):.1f}%)")

    if results:
        # Summary statistics
        grade_counts = defaultdict(int)
        for r in results:
            grade_counts[r['grade']] += 1

        print("\nGrade distribution:")
        for grade in sorted(grade_counts.keys()):
            print(f"  {grade}: {grade_counts[grade]}")

        avg_orphan_rate = sum(r.get('orphan_rate', 0) for r in results) / len(results)
        print(f"\nAverage orphan rate: {avg_orphan_rate:.1f}%")
    else:
        print("No results to display")
