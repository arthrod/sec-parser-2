from __future__ import annotations

# !/usr/bin/env python3

"""Legal Agreement Parser v10 - Fully Integrated with sec-parser for High Precision.

- Utilizes sec-parser's semantic tree building for hierarchy
- Custom nesting rules for legal document structure
- Extended classifiers for domain-specific patterns
- Built-in style metrics for visual analysis
- Composite elements for complex legal structures
- Preserves all v9 precision features while eliminating reinventions
- Tree-based output for accurate hierarchies


"""
from step_tracer import (
    activate_tracing,
    aggregate_orphan_metrics,
    export_traces_to_csv,
    format_trace_summary,
    generate_trace_report,
    get_step_traces,
)

activate_tracing()

import itertools
import operator
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from bs4 import Comment

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
from sec_parser.processing_steps.enhanced_text_element_merger import EnhancedTextElementMerger
from sec_parser.processing_steps.table_classifier import TableClassifier
from sec_parser.processing_steps.table_of_contents_classifier import TableOfContentsClassifier
from sec_parser.processing_steps.text_classifier import TextClassifier
from sec_parser.semantic_elements.abstract_semantic_element import (
    AbstractLevelElement,
    AbstractSemanticElement,
)
from sec_parser.semantic_elements.composite_semantic_element import CompositeSemanticElement
from sec_parser.semantic_elements.semantic_elements import IrrelevantElement, NotYetClassifiedElement, TextElement
from sec_parser.semantic_elements.table_element.table_element import TableElement
from sec_parser.semantic_elements.title_element import TitleElement
from sec_parser.semantic_tree.nesting_rules import AbstractNestingRule
from sec_parser.semantic_tree.semantic_tree import SemanticTree
from sec_parser.semantic_tree.tree_builder import TreeBuilder
from sec_parser.semantic_tree.tree_node import TreeNode

if TYPE_CHECKING:
    from sec_parser.processing_engine.processing_log import LogItemOrigin


DEFAULT_CHAR_DISPLAY_LIMIT = 300
# Global context
_GLOBAL_IS_WDESK = False

# Regular expression for parsing CSS units
_UNIT_RE = re.compile(r"([\d.]+)\s*(pt|px|em|rem|%)?", re.IGNORECASE)


def _get_bs4_tag(html_tag):
    """Safely get the BS4 tag from an HtmlTag object."""
    if hasattr(html_tag, "_bs4"):
        return html_tag._bs4
    return None

# ========================================================================
# CUSTOM SEMANTIC ELEMENTS (Extending sec-parser classes)
# ========================================================================


def _to_pt(value: str, base_pt: float = 12.0) -> float:
    """Convert CSS length to points. Supports pt, px, em, rem, %.
    If unitless -> assume pt. Graceful fallback = 0.
    """
    m = _UNIT_RE.search(value or "")
    if not m:
        return 0.0
    num, unit = float(m.group(1)), m.group(2).lower()
    if unit == "pt":
        return num
    if unit == "px":
        return num * 0.75  # 96 dpi assumption
    if unit in {"em", "rem"}:
        return num * base_pt
    if unit == "%":
        return num * base_pt / 100.0
    return 0.0


class SmartSectionClassifierV6(AbstractElementwiseProcessingStep):
    def __init__(self, types_to_process=None) -> None:
        super().__init__(types_to_process=types_to_process or {NotYetClassifiedElement, TextElement, TableElement})
        self.seen_sections = set()
        self.section_count = 0
        self.article_count = 0

    def _process_element(self, element: AbstractSemanticElement, context: ElementProcessingContext) -> AbstractSemanticElement:
        if not element.html_tag:
            return element
        if element.html_tag.name.lower() == "table":
            result = self._process_table_element(element)
            if result and isinstance(result, (ArticleElement, SectionElement)):
                key = self._get_section_key(result)
                if key not in self.seen_sections:
                    self.seen_sections.add(key)
                    return result
            return result
        text_content = element.html_tag.text.strip()
        result = self._extract_structured_element(text_content, element.html_tag)
        if result and isinstance(result, (ArticleElement, SectionElement)):
            key = self._get_section_key(result)
            if key not in self.seen_sections:
                self.seen_sections.add(key)
                return result
            return element
        return result or element

    def _get_section_key(self, element) -> str:
        if isinstance(element, ArticleElement):
            return f"article:{element.article_number}"
        if isinstance(element, SectionElement):
            return f"section:{element.section_number}"
        return ""

    def _extract_structured_element(self, text: str, html_tag: HtmlTag):
        article_patterns = [r"^(ARTICLE|Article)\s+([IVX]+|\d+)(?:\s*[-\u2013\u2014.]\s*(.*))?"]
        for pattern in article_patterns:
            match = re.match(pattern, text.strip())
            if match:
                self.article_count += 1
                article_num = f"{match.group(1)} {match.group(2)}"
                article_title = match.group(3).strip() if match.group(3) else ""
                return ArticleElement(html_tag, article_number=article_num, article_title=article_title)
        section_patterns = [
            (r"^(Section)\s+(\d+(?:\.\d+)*)(?:\s*[-\u2013\u2014.]\s*(.*))?", "Section"),
            (
                r"""^
                (?P<num>\d+(?:\.\d+)*)            # 1  / 1.2 / 3.4.5
                [\.\)]\s+                         # "." or ")" delimiter
                (?P<title>[A-Z][^a-z]{0,3}        # either ALLCAPS word
                  |[A-Z][a-z].{0,80})             # or normal title < 80 chars
            $""",
                "number",
            ),
        ]
        for pattern, pattern_type in section_patterns:
            match = re.match(pattern, text.strip(), re.VERBOSE if pattern_type == "number" else 0)
            if match:
                self.section_count += 1
                if pattern_type == "Section":
                    section_num = match.group(2)
                    section_title = match.group(3).strip() if match.group(3) else ""
                else:
                    section_num = match.group("num")
                    section_title = match.group("title").strip()
                if len(text) > 120:
                    continue
                level = len(section_num.split(".")) + 1
                return SectionElement(html_tag, section_number=section_num, section_title=section_title, level=level)
        return None

    def _process_table_element(self, element: AbstractSemanticElement):
        try:
            if not hasattr(element.html_tag, "_bs4"):
                return element
            tds = element.html_tag._bs4.find_all("td")
            if len(tds) >= 2:
                first_cell = tds[0].get_text().strip()
                second_cell = tds[1].get_text().strip()
                if re.match(r"^\d+\.?$", first_cell):
                    section_num = first_cell.rstrip(".")
                    section_title = second_cell
                    self.section_count += 1
                    return SectionElement(
                        element.html_tag, section_number=section_num, section_title=section_title, level=2,
                    )
                combined = f"{first_cell} {second_cell}"
                result = self._extract_structured_element(combined, element.html_tag)
                if result:
                    return result
                if self._is_signature_table(first_cell, second_cell):
                    return SignatureBlockElement.create_from_element(element, log_origin=self.__class__.__name__)
        except Exception:
            pass
        return element

    def _is_signature_table(self, cell1: str, cell2: str) -> bool:
        combined_lower = (cell1 + " " + cell2).lower()
        signature_words = [
            "by:",
            "/s/",
            "signature",
            "name:",
            "title:",
            "date:",
            "authorized",
            "executed",
            "witness",
            "acknowledged",
        ]
        word_count = sum(1 for word in signature_words if word in combined_lower)
        return word_count >= 2


class HierarchicalTitleElement(TitleElement):
    """Base for hierarchical titles with custom attributes."""
    def __init__(self, html_tag: HtmlTag, level: int, parent_id: str | None = None, **kwargs) -> None:
        # Remove level from kwargs if it exists to avoid duplicate
        kwargs.pop("level", None)
        kwargs.pop("parent_id", None)  # Remove to avoid passing to parent
        super().__init__(html_tag, level=level, **kwargs)
        self.id = self._generate_id()
        self.parent_id = parent_id
        self.children: list[str] = []  # List of child element IDs
        self.normalized_id_value: str | None = None  # Set in subclasses

    _id_global = itertools.count()  # Unique ID generator

    def _generate_id(self) -> str:
        return f"{self.__class__.__name__}_{next(HierarchicalTitleElement._id_global)}"

    def add_child(self, child_id: str) -> None:
        """Add a child element ID."""
        if child_id not in self.children:
            self.children.append(child_id)

    def normalized_id(self) -> str | None:
        return self.normalized_id_value


class AgreementTitleElement(HierarchicalTitleElement):
    """Main agreement title."""
    def __init__(self, html_tag: HtmlTag, **kwargs) -> None:
        super().__init__(html_tag, level=0, **kwargs)
        self.normalized_id_value = "title"

    @classmethod
    def create_from_element(
        cls,
        source: AbstractSemanticElement,
        log_origin: LogItemOrigin,
        *,
        level: int | None = None,
    ) -> AgreementTitleElement:
        # Ignore the level parameter since AgreementTitleElement always has level=0
        return cls(
            source._html_tag,
            processing_log=source.processing_log,
            log_origin=log_origin,
        )


class ArticleElement(HierarchicalTitleElement):
    """Article-level sections."""
    def __init__(self, html_tag: HtmlTag, article_number: str = "", article_title: str = "", **kwargs) -> None:
        super().__init__(html_tag, level=1, **kwargs)
        self.article_number = article_number
        self.article_title = article_title
        self.normalized_id_value = self._compute_normalized_id()

    def _compute_normalized_id(self) -> str | None:
        if self.article_number:
            match = re.search(r"([IVX]+|\d+)", self.article_number)
            if match:
                return f"article_{match.group(1).lower()}"
        return None


class SectionElement(HierarchicalTitleElement):
    """Numbered sections."""
    def __init__(self, html_tag: HtmlTag, section_number: str = "", section_title: str = "", level: int = 2, **kwargs) -> None:
        super().__init__(html_tag, level=level, **kwargs)
        self.section_number = self._normalize_section_number(section_number)
        self.section_title = section_title
        self.normalized_id_value = self._compute_normalized_id()

    def _normalize_section_number(self, number: str) -> str:
        if re.match(r"^\d+(?:\.\d+)*$", number.strip()):
            return number.strip()
        return number

    def _compute_normalized_id(self) -> str | None:
        if self.section_number:
            match = re.search(r"(\d+(?:\.\d+)*)", self.section_number)
            if match:
                return match.group(1)
        return None


class ClauseElement(HierarchicalTitleElement):
    """Clauses as title elements for hierarchy."""
    def __init__(self, html_tag: HtmlTag, clause_id: str = "", clause_text: str = "", level: int = 3, **kwargs) -> None:
        super().__init__(html_tag, level=level, **kwargs)
        self.clause_id = clause_id
        self.clause_text = clause_text
        self.normalized_id_value = self._compute_normalized_id()

    def _compute_normalized_id(self) -> str | None:
        if self.clause_id:
            match = re.search(r"\(?([a-zA-Z0-9]+)\)?", self.clause_id)
            if match:
                return match.group(1).lower()
        return None


class HeadingElement(HierarchicalTitleElement):
    """Generic headings."""
    def __init__(self, html_tag: HtmlTag, heading_text: str = "", level: int = 1, **kwargs) -> None:
        super().__init__(html_tag, level=level, **kwargs)
        self.heading_text = heading_text


class ContentTextElement(TextElement):
    """Content text with level support."""
    def __init__(self, html_tag: HtmlTag, level: int = 4, **kwargs) -> None:
        super().__init__(html_tag, **kwargs)
        self.level = level


class TableOfContentsElement(CompositeSemanticElement):
    """TOC as composite."""
    def __init__(self, html_tag: HtmlTag, inner_elements: list[AbstractSemanticElement] | None = None, **kwargs) -> None:
        super().__init__(html_tag, inner_elements=tuple(inner_elements or []), **kwargs)

    @classmethod
    def create_from_element(
        cls,
        source: AbstractSemanticElement,
        log_origin: LogItemOrigin,
        **kwargs,
    ) -> TableOfContentsElement:
        # If source is already a CompositeSemanticElement, preserve its inner elements
        # Otherwise, wrap the source element as an inner element
        inner_elements: list[AbstractSemanticElement] = []
        if isinstance(source, CompositeSemanticElement):
            inner_elements = list(source.inner_elements)
        else:
            inner_elements = [source]
        return cls(
            source._html_tag,
            inner_elements=inner_elements,
            processing_log=source.processing_log,
            log_origin=log_origin,
        )


class DefinitionElement(TextElement):
    """Definitions."""
    def __init__(self, html_tag: HtmlTag, term: str = "", definition: str = "", **kwargs) -> None:
        super().__init__(html_tag, **kwargs)
        self.term = term
        self.definition = definition


class PartyElement(TextElement):
    """Parties."""
    def __init__(self, html_tag: HtmlTag, party_name: str = "", party_type: str = "", **kwargs) -> None:
        super().__init__(html_tag, **kwargs)
        self.party_name = party_name
        self.party_type = party_type


class RecitalElement(TextElement):
    """Recitals."""


class SignatureBlockElement(CompositeSemanticElement):
    def __init__(self, html_tag: HtmlTag, inner_elements: list[AbstractSemanticElement] | None = None, **kwargs) -> None:
        super().__init__(html_tag, inner_elements=tuple(inner_elements or []), **kwargs)
    
    @classmethod
    def create_from_element(
        cls,
        source: AbstractSemanticElement,
        log_origin: LogItemOrigin,
        **kwargs,
    ) -> SignatureBlockElement:
        # If source is already a CompositeSemanticElement, preserve its inner elements
        # Otherwise, wrap the source element as an inner element
        inner_elements: list[AbstractSemanticElement] = []
        if isinstance(source, CompositeSemanticElement):
            inner_elements = list(source.inner_elements)
        else:
            inner_elements = [source]
        return cls(
            source._html_tag,
            inner_elements=inner_elements,
            processing_log=source.processing_log,
            log_origin=log_origin,
        )


class ExhibitElement(TitleElement):
    """Exhibits."""
    def __init__(self, html_tag: HtmlTag, exhibit_id: str = "", exhibit_title: str = "", **kwargs) -> None:
        super().__init__(html_tag, level=1, **kwargs)
        self.exhibit_id = exhibit_id
        self.exhibit_title = exhibit_title


# ========================================================================
# CUSTOM PROCESSING STEPS (Extending sec-parser)
# ========================================================================


class HtmlCommentRemoverStep(AbstractProcessingStep):
    def __init__(self) -> None:
        super().__init__()
        self._stats: dict[str, int] = {"comments_removed": 0}

    def _process(self, elements: list[AbstractSemanticElement], context=None) -> list[AbstractSemanticElement]:
        filtered = []
        removed_count = 0
        for el in elements:
            if hasattr(el, "html_tag") and getattr(el.html_tag, "_bs4", None):
                if isinstance(el.html_tag.get_bs4(), Comment):
                    removed_count += 1
                    continue
            filtered.append(el)
        self._stats["comments_removed"] += removed_count
        return filtered

    def get_stats(self) -> dict[str, int]:
        return self._stats


class SignatureMetadataRemover(AbstractElementwiseProcessingStep):
    _SIGNATURE_METADATA_PATTERNS = [
        r"DocuSign\s+Envelope\s+ID:\s*[A-F0-9-]+",
        r"Envelope\s+ID:\s*[A-F0-9-]+",
        r"DocuSign\s+Certificate\s+of\s+Completion",
        # HelloSign patterns
        r"HelloSign\s+Signature\s+ID:\s*[a-f0-9]+",
        r"HelloSign\s+Document\s+ID:\s*[a-f0-9]+",
        # PandaDoc patterns
        r"PandaDoc\s+Document\s+ID:\s*[a-f0-9-]+",
        r"PandaDoc\s+Audit\s+Trail",
        # Generic e-signature patterns
        r"Electronic\s+Signature\s+Certificate",
        r"Digital\s+Signature\s+Summary",
        r"Signature\s+Verification\s+Report",
        r"Certificate\s+of\s+Completion",
        # Timestamp and tracking patterns
        r"Signed\s+on:\s*\d{1,2}/\d{1,2}/\d{4}\s+\d{1,2}:\d{2}:\d{2}",
        r"Completed\s+on:\s*\d{1,2}/\d{1,2}/\d{4}",
        r"IP\s+Address:\s*\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}",
        # Security codes and hashes
        r"Security\s+Code:\s*[A-F0-9]+",
        r"Verification\s+Hash:\s*[a-f0-9]+",
        r"Authentication\s+Code:\s*[A-F0-9-]+",
    ]

    def __init__(self, *, types_to_process=None, types_to_exclude=None) -> None:
        super().__init__(types_to_process=types_to_process, types_to_exclude=types_to_exclude)
        self._compiled_patterns = [re.compile(p, re.IGNORECASE | re.MULTILINE) for p in self._SIGNATURE_METADATA_PATTERNS]

    def _process_element(self, element: AbstractSemanticElement, context: ElementProcessingContext) -> AbstractSemanticElement:
        if self._is_signature_metadata(element):
            element.processing_log.add_item(message="Identified as e-signature metadata", log_origin=self.__class__.__name__)
            return IrrelevantElement.create_from_element(element, log_origin=self.__class__.__name__)
        return element

    def _is_signature_metadata(self, element: AbstractSemanticElement) -> bool:
        if not hasattr(element, "text") or not element.text:
            return False
        text = element.text.strip()
        if len(text) < 10:
            return False
        for pattern in self._compiled_patterns:
            if pattern.search(text):
                return True
        return self._check_additional_heuristics(text)

    def _check_additional_heuristics(self, text: str) -> bool:
        text_lower = text.lower()
        esig_keywords = ["envelope", "signature", "signed", "certificate", "completion", "verification", "authentication", "digital", "electronic"]
        keyword_count = sum(1 for keyword in esig_keywords if keyword in text_lower)
        if keyword_count >= 2:
            return True
        uuid_pattern = re.compile(r"[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}", re.IGNORECASE)
        if uuid_pattern.search(text):
            return True
        return bool(len(text) > 50 and re.match(r"^[A-Za-z0-9+/=\s]+$", text) and text.count("\n") < 3)


class HierarchicalElement(AbstractSemanticElement):
    """Base class for hierarchical elements with parent/child relationships."""

    _id_global = itertools.count()  # v9: Use itertools for ID generation

    def __init__(self, html_tag: HtmlTag, parent_id: str | None = None, level: int = 0, **kwargs) -> None:
        super().__init__(html_tag, **kwargs)
        self.parent_id = parent_id
        self.children: list[str] = []  # List of child element IDs
        self.level = level
        self.id = self._generate_id()

    def _generate_id(self) -> str:
        """Generate unique ID for this element using itertools."""
        return f"{self.__class__.__name__}_{next(HierarchicalElement._id_global)}"

    def add_child(self, child_id: str) -> None:
        """Add a child element ID."""
        if child_id not in self.children:
            self.children.append(child_id)

    def normalized_id(self) -> str | None:
        """Return normalized ID for cross-reference indexing."""
        return None  # Override in subclasses


# Custom Nesting Rule
class LegalDocumentNestingRule(AbstractNestingRule):
    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)

    _ALLOWED = {
        ArticleElement: {SectionElement, DefinitionElement, ClauseElement},
        SectionElement: {ClauseElement, HeadingElement, ContentTextElement},
        ClauseElement: {ContentTextElement, ClauseElement},
        HeadingElement: {ContentTextElement, HeadingElement, ClauseElement, SectionElement},
        TableElement: {ContentTextElement, SectionElement, ClauseElement},
        TableOfContentsElement: {SectionElement, HeadingElement},
    }

    def _should_be_nested_under(self, parent: AbstractSemanticElement, child: AbstractSemanticElement) -> bool:
        if isinstance(parent, AbstractLevelElement) and isinstance(child, AbstractLevelElement):
            if parent.level < child.level:
                allowed = self._ALLOWED.get(type(parent), set())
                return type(child) in allowed
        if isinstance(child, ContentTextElement):
            return type(child) in self._ALLOWED.get(type(parent), set())
        return False


# Indentation Rule (fallback)
class IndentationNestingRule(AbstractNestingRule):
    def __init__(self, indent_threshold=20.0) -> None:
        super().__init__()
        self.indent_threshold = indent_threshold

    def _should_be_nested_under(self, parent: AbstractSemanticElement, child: AbstractSemanticElement) -> bool:
        parent_indent = self._get_indent(parent)
        child_indent = self._get_indent(child)
        return child_indent > parent_indent + self.indent_threshold

    def _get_indent(self, element: AbstractSemanticElement) -> float:
        if not element.html_tag:
            return 0.0
        style_metrics = element.html_tag.get_text_styles_metrics()
        margin_left = style_metrics.get(("margin-left", ""), 0)
        padding_left = style_metrics.get(("padding-left", ""), 0)
        return margin_left + padding_left


class PageContinuationMerger(AbstractProcessingStep):
    _END_PUNCT = re.compile(r'[.!?;:)\]\"\'\u2019]$')

    def _looks_incomplete(self, txt: str) -> bool:
        if len(txt) < 20:
            return False
        if txt.endswith(("-", "\u00ad")):
            return True
        if self._END_PUNCT.search(txt):
            return False
        return not txt.isupper()

    def _process(self, elements, ctx=None):
        out, i = [], 0
        while i < len(elements):
            cur = elements[i]
            if isinstance(cur, (TextElement, ContentTextElement)):
                j = i + 1
                while j < len(elements) and isinstance(elements[j], MetadataElement):
                    j += 1
                if (
                    j < len(elements)
                    and isinstance(elements[j], (TextElement, ContentTextElement))
                    and self._looks_incomplete(cur.html_tag.text.strip())
                ):
                    nxt = elements[j]
                    first, second = cur.html_tag, nxt.html_tag
                    merged = first.text.rstrip("-\u00ad") + " " + second.text.lstrip()
                    new_html_tag = HtmlTag.clone_with_text(first, merged)
                    if isinstance(cur, TextElement):
                        cur = ContentTextElement(new_html_tag, level=getattr(cur, "level", 4))
                    else:
                        cur = type(cur)(new_html_tag, level=getattr(cur, "level", 4))
                    i = j + 1
                    out.append(cur)
                    continue
            out.append(cur)
            i += 1
        return out


class ConsecutivePageNumberClassifier(AbstractProcessingStep):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._stats = {"consecutive_pages_removed": 0}

    _roman = re.compile(
        r"^(?=[IVXLCDM])M{0,4}(CM|CD|D?C{0,3})(XC|XL|L?X{0,3})(IX|IV|V?I{0,3})$",
        re.IGNORECASE,
    )

    def _is_page_digit(self, txt: str) -> bool:
        txt = txt.strip(" -()")
        if len(txt) > 3:
            return False
        return txt.isdigit() or bool(self._roman.fullmatch(txt))

    def _process(self, elements: list[AbstractSemanticElement]) -> list[AbstractSemanticElement]:
        i = 0
        out = []
        consecutive_count = 0
        while i < len(elements):
            window = elements[i : i + 3]
            if len(window) == 3 and all(
                isinstance(el, TextElement) and el.html_tag and self._is_page_digit(el.html_tag.text) for el in window
            ):
                out.extend(PageNumberMetadataElement(el.html_tag) for el in window)
                consecutive_count += 3
                i += 3
            else:
                out.append(elements[i])
                i += 1
        self._stats["consecutive_pages_removed"] += consecutive_count
        return out

    def get_stats(self) -> dict[str, int]:
        return getattr(self, "_stats", {"consecutive_pages_removed": 0})


# Metadata elements
class MetadataElement(IrrelevantElement):
    metadata_type = "generic"

    @classmethod
    def create_from_element(
        cls,
        source: AbstractSemanticElement,
        log_origin: LogItemOrigin,
        **kwargs,
    ) -> MetadataElement:
        return cls(
            source._html_tag,
            processing_log=source.processing_log,
        )


class RepeatedHeaderElement(MetadataElement):
    metadata_type = "repeated_header"


class ExhibitStampElement(MetadataElement):
    metadata_type = "exhibit_stamp"


class ExecutionStampElement(MetadataElement):
    metadata_type = "execution_stamp"


class PageNumberMetadataElement(MetadataElement):
    metadata_type = "page_number"


class SignaturePageFollowsElement(MetadataElement):
    metadata_type = "signature_follows"


class PageHeaderElement(MetadataElement):
    metadata_type = "page_header"
    metadata_stats: defaultdict[str, int] = defaultdict(int)

    def get_stats(self) -> dict[str, int]:
        return dict(self.metadata_stats)


class TitleClassifier(AbstractElementBatchProcessingStep):
    _METADATA_PATTERNS = [
        r"^\s*exhibit\s+\d+(\.\d+)?\s*$",
        r"^\s*attachment\s+[a-z]\s*$",
        r"^\s*schedule\s+[a-z]\s*$",
        r"^\s*execution\s+version\s*$",
        r"^\s*draft\s*$",
        r"^\s*confidential\s*$",
        r"^\s*preliminary\s*$",
        r"^\s*proprietary\s*$",
        r"^\s*[a-z]*\s*copy\s*$",
    ]

    _TITLE_PATTERNS = [
        r"\b(agreement|contract|lease|deed|note|license|policy)\b",
        r"\b(amendment|addendum|supplement|modification)\b",
        r"\b(memorandum|memo|letter|notice)\b",
        r"\b(terms|conditions|provisions)\b",
        r"\b(loan|credit|security|mortgage)\b",
        r"\b(employment|service|consulting)\b",
        r"\b(purchase|sale|acquisition|merger)\b",
        r"\b(partnership|joint\s+venture|llc)\b",
        r"\b(non-disclosure|nda|confidentiality)\b",
        r"\b(subscription|investment|equity)\b",
    ]

    def __init__(self, *, types_to_process=None, types_to_exclude=None) -> None:
        super().__init__()
        self.types_to_process = types_to_process
        self.types_to_exclude = types_to_exclude
        self._metadata_patterns = [re.compile(pattern, re.IGNORECASE | re.MULTILINE) for pattern in self._METADATA_PATTERNS]
        self._title_patterns = [re.compile(pattern, re.IGNORECASE) for pattern in self._TITLE_PATTERNS]

    def _process_elements(
        self, elements: list[AbstractSemanticElement], context: ElementProcessingContext,
    ) -> list[AbstractSemanticElement]:
        if len(elements) <= 10:
            initial_elements = elements
            remaining_elements = []
        else:
            initial_elements = elements[:10]
            remaining_elements = elements[10:]
        processed_initial = self._classify_initial_elements(initial_elements)
        return processed_initial + remaining_elements

    def _classify_initial_elements(self, elements: list[AbstractSemanticElement]) -> list[AbstractSemanticElement]:
        if not elements:
            return elements
        potential_metadata = []
        potential_titles = []
        for i, element in enumerate(elements):
            if not hasattr(element, "text") or not element.text:
                continue
            text = element.text.strip()
            if not text:
                continue
            if self._is_metadata(text):
                potential_metadata.append((i, element))
            elif self._is_likely_title(text):
                potential_titles.append((i, element))
        return self._apply_classification_logic(elements, potential_metadata, potential_titles)

    def _is_metadata(self, text: str) -> bool:
        for pattern in self._metadata_patterns:
            if pattern.search(text):
                return True
        text_lower = text.lower().strip()
        if len(text) < 30 and text.isupper():
            return True
        metadata_keywords = [
            "version",
            "draft",
            "copy",
            "confidential",
            "proprietary",
            "preliminary",
            "exhibit",
            "attachment",
            "schedule",
        ]
        return bool(any(keyword in text_lower for keyword in metadata_keywords))

    def _is_likely_title(self, text: str) -> bool:
        for pattern in self._title_patterns:
            if pattern.search(text):
                return True
        text_lower = text.lower()
        if len(text) > 20 and len(text) < 200:
            words = text.split()
            if len(words) >= 3:
                capitalized_words = sum(1 for word in words if word[0].isupper())
                if capitalized_words >= len(words) * 0.6:
                    return True
        business_indicators = [
            "business",
            "company",
            "corporation",
            "inc",
            "llc",
            "ltd",
            "between",
            "among",
            "party",
            "parties",
        ]
        return bool(any(indicator in text_lower for indicator in business_indicators))

    def _apply_classification_logic(
        self,
        elements: list[AbstractSemanticElement],
        potential_metadata: list[tuple[int, AbstractSemanticElement]],
        potential_titles: list[tuple[int, AbstractSemanticElement]],
    ) -> list[AbstractSemanticElement]:
        result = elements.copy()
        if potential_metadata and potential_titles:
            title_idx, title_element = potential_titles[0]
            for meta_idx, meta_element in potential_metadata:
                if meta_idx < title_idx:
                    result[meta_idx] = self._create_metadata_element(meta_element)
            result[title_idx] = self._create_title_element(title_element)
            for idx, later_title in potential_titles[1:]:
                result[idx] = self._create_metadata_element(later_title)
        elif potential_titles and not potential_metadata:
            title_idx, title_element = potential_titles[0]
            result[title_idx] = self._create_title_element(title_element)
            for idx, later_title in potential_titles[1:]:
                result[idx] = self._create_metadata_element(later_title)
        elif potential_metadata and not potential_titles:
            first_meta_idx, first_meta_element = potential_metadata[0]
            if self._is_exhibit_document(first_meta_element.text if hasattr(first_meta_element, "text") else ""):
                result[first_meta_idx] = self._create_title_element(first_meta_element)
            else:
                result[first_meta_idx] = self._create_metadata_element(first_meta_element)
        return result

    def _is_exhibit_document(self, text: str) -> bool:
        text_lower = text.lower().strip()
        exhibit_patterns = [r"^\s*exhibit\s+\d+(\.\d+)?\s*$", r"^\s*exhibit\s+[a-z]\s*$"]
        return any(re.match(pattern, text_lower) for pattern in exhibit_patterns)

    def _create_metadata_element(self, element: AbstractSemanticElement) -> MetadataElement:
        """Create a MetadataElement from an existing element.

        Args:
            element: The element to convert

        Returns:
            A new MetadataElement
        """
        element.processing_log.add_item(message="Classified as document metadata", log_origin=self.__class__.__name__)
        return MetadataElement.create_from_element(element, log_origin=self.__class__.__name__)

    def _create_title_element(self, element: AbstractSemanticElement) -> AgreementTitleElement:
        element.processing_log.add_item(message="Classified as document title", log_origin=self.__class__.__name__)
        return AgreementTitleElement.create_from_element(element, log_origin=self.__class__.__name__)


class RepeatedHeaderFooterDetector(AbstractProcessingStep):
    MAX_LEN = 90
    MIN_REPEATS = 3

    def _process(self, elements: list[AbstractSemanticElement]) -> list[AbstractSemanticElement]:
        counter = defaultdict(int)
        for el in elements:
            txt = getattr(el.html_tag, "text", "").strip() if hasattr(el, "html_tag") else ""
            txt_norm = re.sub(r"LEGAL_US_E # \d+\.\d+", "LEGAL_US_E", txt)
            if 3 <= len(txt_norm) <= self.MAX_LEN:
                counter[txt_norm] += 1
        repeated = {t for t, c in counter.items() if c >= self.MIN_REPEATS}

        out: list[AbstractSemanticElement] = []
        for el in elements:
            if isinstance(el, TextElement):
                txt_original = el.html_tag.text.strip()
                txt_norm = re.sub(r"LEGAL_US_E # \d+\.\d+", "LEGAL_US_E", txt_original)
                if txt_norm in repeated:
                    out.append(RepeatedHeaderElement(el.html_tag))
                    continue
            out.append(el)
        return out


class EnhancedTOCDetector(AbstractElementwiseProcessingStep):
    def __init__(
        self,
        *, types_to_process: set[type[AbstractSemanticElement]] | None = None,
        types_to_exclude: set[type[AbstractSemanticElement]] | None = None,
    ) -> None:
        super().__init__()
        self.in_toc_section = False
        self.toc_line_count = 0

    def _process_element(
        self, element: AbstractSemanticElement, context: ElementProcessingContext,
    ) -> AbstractSemanticElement:
        if not element.html_tag:
            return element
        if isinstance(element, TableElement) and self._is_toc_table(element):
            return TableOfContentsElement.create_from_element(element, log_origin=self.__class__.__name__)
        text = element.html_tag.text.strip()
        if self._is_toc_header(text):
            self.in_toc_section = True
            self.toc_line_count = 0
            return TableOfContentsElement.create_from_element(element, log_origin=self.__class__.__name__)

        # If we're in a TOC section, check for TOC lines
        if self.in_toc_section and isinstance(element, TextElement):
            if self._is_toc_line(text):
                self.toc_line_count += 1
                section_info = self._extract_toc_section_info(text)
                if section_info:
                    num, title = section_info
                    return SectionElement(
                        element.html_tag,
                        section_number=num,
                        section_title=title,
                        level=1,
                    )
                return element
            if self.toc_line_count > 3:
                self.in_toc_section = False
                self.toc_line_count = 0
        return element

    def _is_toc_table(self, element: TableElement) -> bool:
        try:
            if not hasattr(element.html_tag, "_bs4"):
                return False
            table = element.html_tag.get_bs4()
            text_content = table.get_text().lower()
            toc_indicators = ["table of contents", "contents", "index", "page"]
            indicator_count = sum(1 for indicator in toc_indicators if indicator in text_content)
            has_page_nums = bool(re.search(r"\b\d{1,3}\b", text_content))
            return indicator_count >= 2 or ("contents" in text_content and has_page_nums)
        except Exception:
            return False

    def _is_toc_header(self, text: str) -> bool:
        toc_patterns = [r"^TABLE\s+OF\s+CONTENTS?$", r"^Contents?$", r"^INDEX$"]
        return any(re.match(pattern, text.strip(), re.IGNORECASE) for pattern in toc_patterns)

    def _is_toc_line(self, text: str) -> bool:
        toc_line_patterns = [
            r"^.+\.{3,}\s*\d+$",
            r"^.+\s{5,}\d+$",
            r"^\d+\.\s+.+\s+\d+$",
            r"^[A-Z]+\.\s+.+\s+\d+$",
        ]
        return any(re.match(pattern, text.strip()) for pattern in toc_line_patterns)

    def _extract_toc_section_info(self, text: str) -> tuple[str, str] | None:
        patterns = [
            r"^(\d+(?:\.\d+)*)\.\s+([^.\s].+?)(?:\.{3,}|\s{5,})\d+$",
            r"^Section\s+(\d+(?:\.\d+)*)\s*[-\u2013\u2014]\s*([^.\s].+?)\s+\d+$",
            r"^([IVX]+)\.\s+([^.\s].+?)(?:\.{3,}|\s{5,})\d+$",
        ]
        for pattern in patterns:
            match = re.match(pattern, text.strip())
            if match:
                return (match.group(1), match.group(2).strip())
        return None


class VisualHeadingDetector(AbstractElementwiseProcessingStep):
    _SECTION_RE = re.compile(
        r"""^(?P<num>\d+(?:\.\d+)*)
            [\.\)]?\s*[-\u2013\u2014]?\s+
            (?P<title>(?!continued\b|page\b|table\b)[^.;]{3,100})$""",
        re.IGNORECASE | re.VERBOSE,
    )

    _ARTICLE_RE = re.compile(
        r"""^ARTICLE\s+
            (?P<num>[IVXLCDM]+|\d+)
            (?:\s*[-\u2013\u2014.]?\s*
            (?P<title>[^.;]{3,80}))?
        $""",
        re.IGNORECASE | re.VERBOSE,
    )

    def __init__(
        self,
        *, types_to_process: set[type[AbstractSemanticElement]] | None = None,
        types_to_exclude: set[type[AbstractSemanticElement]] | None = None,
    ) -> None:
        super().__init__()

    def _process_element(
        self, element: AbstractSemanticElement, context: ElementProcessingContext,
    ) -> AbstractSemanticElement:
        if not isinstance(element, TextElement):
            return element
        tag = element.html_tag.get_bs4() if hasattr(element.html_tag, "_bs4") else None
        if not tag:
            return element
        if tag.name and tag.name.lower() in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            lvl = int(tag.name[1])
            return HeadingElement(element.html_tag, element.text, level=lvl)
        metrics = element.html_tag.get_text_styles_metrics()
        bold_pct = sum(v for (k, v) in metrics.items() if k[0] == "font-weight" and k[1] in {"bold", "700", "800", "900"})
        mtop_pct = metrics.get(("margin-top", ""), 0)
        global _GLOBAL_IS_WDESK
        if _GLOBAL_IS_WDESK:
            if not (bold_pct > 0.5 or mtop_pct > 0.3):
                return element
        elif not (bold_pct > 0.8 and mtop_pct > 0.5):
            return element
        txt = re.sub(r"\s+", " ", element.text.strip())
        if len(txt.split()) > 18:
            return element
        if re.match(r"^\(?[a-z]\)|^[ivxlcdm]+\)", txt, re.IGNORECASE):
            return element
        am = self._ARTICLE_RE.match(txt)
        if am:
            num = am.group("num")
            title = (am.group("title") or "").strip()
            return ArticleElement(element.html_tag, article_number=num, article_title=title)
        sm = self._SECTION_RE.match(txt)
        if sm:
            sec_num = sm.group("num")
            title = sm.group("title").strip()
            lvl = len(sec_num.split(".")) + 1
            return SectionElement(element.html_tag, section_number=sec_num, section_title=title, level=lvl)
        return HeadingElement(element.html_tag, heading_text=txt, level=2)


class HierarchyBuilder(AbstractProcessingStep):
    """Build hierarchical relationships between elements."""

    def __init__(
        self,
        *,
        types_to_process: set[type[AbstractSemanticElement]] | None = None,
        types_to_exclude: set[type[AbstractSemanticElement]] | None = None,
    ) -> None:
        super().__init__()
        self.element_stack: list[HierarchicalElement] = []
        self.id_map: dict[str, HierarchicalElement] = {}

    def _process(self, elements: list[AbstractSemanticElement]) -> list[AbstractSemanticElement]:
        """Build hierarchy for all elements."""
        hierarchical_elements = []

        for element in elements:
            if isinstance(element, HierarchicalElement):
                self._build_hierarchy(element)
                hierarchical_elements.append(element)
                self.id_map[element.id] = element
            else:
                hierarchical_elements.append(element)

        # Step 11: Add fallback indentation hierarchy if insufficient heading structure
        head_count = len([
            e for e in hierarchical_elements if isinstance(e, (HeadingElement, SectionElement, ArticleElement))
        ])
        if head_count < 3:
            hierarchical_elements = self.apply_indentation_heuristic(hierarchical_elements)

        return hierarchical_elements

    def process(self, elements: list[AbstractSemanticElement]) -> list[AbstractSemanticElement]:
        """Public interface - delegates to _process for consistency."""
        return self._process(elements)

    def _build_hierarchy(self, element: HierarchicalElement) -> None:
        """Build parent-child relationships."""
        # Find appropriate parent based on level
        parent = None

        # Pop elements from stack until we find a valid parent
        while self.element_stack:
            potential_parent = self.element_stack[-1]
            if potential_parent.level < element.level:
                parent = potential_parent
                break
            self.element_stack.pop()

        # Set parent relationship
        if parent:
            # Only set parent_id if the element supports it
            if hasattr(element, "parent_id"):
                element.parent_id = parent.id
            if hasattr(parent, "add_child"):
                parent.add_child(element.id)

        # Add to stack
        self.element_stack.append(element)

    def apply_indentation_heuristic(self, elements: list[AbstractSemanticElement]) -> list[AbstractSemanticElement]:
        """Apply indentation-based hierarchy when insufficient headings are detected."""
        # Get elements that might benefit from indentation hierarchy
        text_elements = [
            e for e in elements if isinstance(e, (TextElement, ContentTextElement)) and hasattr(e.html_tag, "_bs4")
        ]

        if len(text_elements) < 2:
            return elements

        # Extract indentation levels from margin-left styles
        indented_elements = []
        for element in text_elements:
            tag = _get_bs4_tag(element.html_tag)
            if tag:
                # Simple style extraction without computed_style
                style_attr = tag.get("style", "")
                margin_left = 0
                text_indent = 0
                if style_attr and isinstance(style_attr, str):
                    # Extract margin-left
                    m = re.search(r"margin-left:\s*([^;]+)", style_attr)
                    if m:
                        margin_left = _to_pt(m.group(1))
                    # Extract text-indent
                    m = re.search(r"text-indent:\s*([^;]+)", style_attr)
                    if m:
                        text_indent = _to_pt(m.group(1))
                total_indent = margin_left + text_indent
                indented_elements.append((element, total_indent))

        if len(indented_elements) < 2:
            return elements

        # Sort by indentation to create tiers
        indented_elements.sort(key=operator.itemgetter(1))

        # Group into indentation tiers (every 20pt is a new level)
        INDENT_THRESHOLD = 20.0
        indent_levels = {}
        current_level = 1
        last_indent = indented_elements[0][1]

        for element, indent in indented_elements:
            if indent > last_indent + INDENT_THRESHOLD:
                current_level += 1
                last_indent = indent
            indent_levels[element] = current_level

        # Convert qualifying text elements to hierarchical elements based on indentation
        result_elements: list[AbstractSemanticElement] = []
        parent_stack = []  # Stack of (level, element) tuples

        for element in elements:
            if element in indent_levels:
                level = indent_levels[element]
                # Create a simple heading element based on indentation
                if len(element.html_tag.text.strip()) > 10:  # Only promote substantial text
                    heading = HeadingElement(element.html_tag, heading_text=element.html_tag.text.strip(), level=level)

                    # Build parent-child relationships
                    while parent_stack and parent_stack[-1][0] >= level:
                        parent_stack.pop()

                    if parent_stack:
                        parent = parent_stack[-1][1]
                        if hasattr(heading, "parent_id") and hasattr(parent, "id"):
                            heading.parent_id = parent.id
                        if hasattr(parent, "add_child") and hasattr(heading, "id"):
                            parent.add_child(heading.id)

                    parent_stack.append((level, heading))
                    result_elements.append(heading)
                else:
                    result_elements.append(element)
            else:
                result_elements.append(element)

        return result_elements


class EarlyMetadataRemoverStep(AbstractProcessingStep):
    _trash = re.compile(
        r"""
        ^\s*Field:\s*(?:Rule-)?Page.*$ |        # Rule-Page, Page; Sequence
        ^\s*ZEQ\.\=1,SEQ=\d+.*$   |             # inline-XBRL audit rows
        ^\s*\*+\s*Text\s+Omitted.*$ |           # ***Text Omitted… SEC redactions
        ^\s*A-\d+\s*$ |                         # appendix page labels
        Field:\s*(?:Rule-)?Page                 # anywhere in text
        """,
        re.IGNORECASE | re.VERBOSE,
    )

    def _process(self, elements, context=None):
        filtered = []
        for el in elements:
            if isinstance(el, TextElement) and el.html_tag and el.html_tag.text:
                if self._trash.match(el.html_tag.text.strip()):
                    continue
            filtered.append(el)
        return filtered


class LateMetadataRemoverStep(AbstractProcessingStep):
    _trash = re.compile(
        r"""
        Field:\s*(?:Rule-)?Page |               # Page field markers
        ZEQ\.\=1,SEQ= |                         # XBRL audit rows
        \*+\s*Text\s+Omitted |                  # SEC redactions
        ^\s*A-\d+\s*$ |                         # appendix page labels
        Page\s+\d+\s+of\s+\d+ |                 # page footers
        ^\s*-\s*\d+\s*-\s*$                     # page numbers
        """,
        re.IGNORECASE | re.VERBOSE,
    )

    def _process(self, elements, context=None):
        filtered = []
        for el in elements:
            if hasattr(el, "html_tag") and el.html_tag and el.html_tag.text:
                text = el.html_tag.text.strip()
                if self._trash.search(text) and len(text) < 200:
                    continue
            filtered.append(el)
        return filtered


class TableRootPromoter(AbstractProcessingStep):
    MIN_PARA_LEN = 15
    LOOKAHEAD = 5

    def _process(self, elements, context=None):
        first_content_idx = -1
        for i, el in enumerate(elements):
            if not isinstance(el, (MetadataElement, IrrelevantElement)):
                first_content_idx = i
                break
        if first_content_idx == -1:
            return elements
        if isinstance(elements[first_content_idx], TableElement) and first_content_idx + self.LOOKAHEAD < len(elements):
            next_elements = elements[first_content_idx + 1 : first_content_idx + 1 + self.LOOKAHEAD]
            paragraph_like = all(
                isinstance(el, (TextElement, ContentTextElement))
                and el.html_tag
                and len(el.html_tag.text.strip()) >= self.MIN_PARA_LEN
                for el in next_elements
            )
            if paragraph_like:
                table = elements[first_content_idx]
                section = SectionElement(table.html_tag, section_number="1", section_title="Table Section", level=1)
                elements[first_content_idx] = section
        return elements


class FallbackTitleClassifier(AbstractProcessingStep):
    KNOWN_PREFIX = re.compile(r"^(Exhibit|Schedule|Appendix)\s+\d+[A-Z]?\b", re.IGNORECASE)

    def _process(self, elements: list[AbstractSemanticElement]) -> list[AbstractSemanticElement]:
        if any(isinstance(el, AgreementTitleElement) for el in elements):
            return elements
        for i, el in enumerate(elements):
            if isinstance(el, TextElement) and el.html_tag and el.html_tag.text:
                if self.KNOWN_PREFIX.match(el.html_tag.text.strip()):
                    new = AgreementTitleElement(html_tag=el.html_tag)
                    elements[i] = new
                    break
        return elements


class ImprovedMetadataRemoverv8(AbstractElementwiseProcessingStep):
    def __init__(
        self,
        *, types_to_process: set[type[AbstractSemanticElement]] | None = None,
        types_to_exclude: set[type[AbstractSemanticElement]] | None = None,
    ) -> None:
        super().__init__()
        self.metadata_stats: defaultdict[str, int] = defaultdict(int)
        self._field_tokens = {"field:", "page", "sequence", "options", "value", "rule-page", "last"}

    def _is_short_and_mostly_field_tokens(self, text: str) -> bool:
        if len(text) > 120:
            return False
        tokens = re.split(r"[\s;:]+", text.lower())
        real_words = [t for t in tokens if t.isalpha() and t not in self._field_tokens]
        return len(real_words) <= 2

    def _process_element(
        self, element: AbstractSemanticElement, context: ElementProcessingContext,
    ) -> AbstractSemanticElement:
        if not element.html_tag:
            return element
        text_content = element.html_tag.text.strip()
        metadata_result = self._identify_metadata(text_content)
        if metadata_result:
            metadata_type, metadata_class = metadata_result
            self.metadata_stats[metadata_type] += 1
            return metadata_class(element.html_tag)
        return element

    def _identify_metadata(self, text: str) -> tuple[str, type] | None:
        text_stripped = text.strip()
        text_lower = text.lower().strip()
        if re.fullmatch(r"Execution\s+Version", text_stripped, re.IGNORECASE):
            return ("execution_stamp", ExecutionStampElement)
        if re.fullmatch(r"Exhibit\s+\d+(\.\d+)?([\-\u2013]\d+)?", text_stripped, re.IGNORECASE):
            return ("exhibit_stamp", ExhibitStampElement)
        if text_lower.startswith("field:") and self._is_short_and_mostly_field_tokens(text_stripped):
            return ("page_number", PageNumberMetadataElement)
        if text_stripped.startswith("PROfilePageNumberReset%"):
            return ("page_number", PageNumberMetadataElement)
        if re.fullmatch(r"[a-z0-9_\-]+\.(?:jpe?g|png|gif)", text_stripped, re.IGNORECASE):
            return ("page_number", PageNumberMetadataElement)
        if re.fullmatch(r"\[?\*{3,}\]?", text_stripped):
            return ("redaction_stamp", ExhibitStampElement)
        exhibit_patterns = [
            r"^Exhibit\s+\d+(\.\d+)?(?:\s|$)",
            r"^EX-?\d+(\.\d+)?(?:\s|$)",
            r"^EXHIBIT\s+[A-Z0-9]+(?:\s|$)",
            r"^Schedule\s+[A-Z0-9]+(?:\s|$)",
            r"^Annex\s+[A-Z0-9]+(?:\s|$)",
            r"^Appendix\s+[A-Z0-9]+(?:\s|$)",
            r"^Attachment\s+[A-Z0-9]+(?:\s|$)",
        ]
        for pattern in exhibit_patterns:
            if re.match(pattern, text_stripped, re.IGNORECASE):
                return ("exhibit_stamp", ExhibitStampElement)
        page_patterns = [
            (r"^Page\s+\d+\s+of\s+\d+$", 20),
            (r"^-\s*\d+\s*-$", 10),
            (r"^\d+$", 3),
            (r"^PAGE\s+\d+$", 10),
            (r"^\[\s*\d+\s*\]$", 10),
            (r"^Page\s+\d+$", 10),
            (r"^\d+\s+of\s+\d+$", 10),
        ]
        for pattern, max_len in page_patterns:
            if re.match(pattern, text_stripped, re.IGNORECASE) and len(text_stripped) <= max_len:
                return ("page_number", PageNumberMetadataElement)
        return None


class EnhancedClauseClassifierV6(AbstractElementwiseProcessingStep):
    def __init__(self, types_to_process=None) -> None:
        super().__init__(types_to_process=types_to_process or {NotYetClassifiedElement, TextElement, TableElement})
        self.clause_count = 0
        self._seen = set()

    def _process_element(self, element: AbstractSemanticElement, context: ElementProcessingContext) -> AbstractSemanticElement:
        if not element.html_tag:
            return element

        # Special handling for TableElement
        if isinstance(element, TableElement) and hasattr(element.html_tag, "_bs4"):
            tds = element.html_tag._bs4.find_all("td")
            if len(tds) >= 2:
                # Check if second cell contains clause pattern (like "1.1.1")
                second_cell = tds[1].get_text().strip()
                # Also check for clause patterns with the content from remaining cells
                remaining_text = " ".join(td.get_text().strip() for td in tds[2:]) if len(tds) > 2 else ""
                combined_text = f"{second_cell} {remaining_text}".strip()

                # Check if the second cell matches clause numbering patterns
                if re.match(r"^\d+(?:\.\d+)+$", second_cell):
                    # This is a clause number like "1.1.1"
                    clause_info = (second_cell, remaining_text, 3)  # Default level 3 for dotted clauses
                else:
                    # Try standard clause extraction
                    clause_info = self._extract_clause(combined_text)

                if clause_info:
                    clause_id, clause_text, level = clause_info
                    normalized = clause_id.strip("().")
                    parent_id = getattr(element, "parent_id", None)  # Now from tree
                    key = (parent_id, normalized)
                    if key in self._seen:
                        return element  # Return original element instead of None
                    self._seen.add(key)
                    self.clause_count += 1
                    return ClauseElement(element.html_tag, clause_id=clause_id, clause_text=clause_text, level=level)

        # Standard text processing
        text_content = element.html_tag.text.strip()
        clause_info = self._extract_clause(text_content)
        if clause_info:
            clause_id, clause_text, level = clause_info
            normalized = clause_id.strip("().")
            parent_id = getattr(element, "parent_id", None)  # Now from tree
            key = (parent_id, normalized)
            if key in self._seen:
                return element  # Return original element instead of None
            self._seen.add(key)
            self.clause_count += 1
            return ClauseElement(element.html_tag, clause_id=clause_id, clause_text=clause_text, level=level)
        return element

    def _extract_clause(self, text: str) -> tuple[str, str, int] | None:
        clause_patterns = [
            (r"^\(([a-z])\)(?:\s+(.*))?", 3),
            (r"^\(([A-Z])\)(?:\s+(.*))?", 4),
            (r"^\((\d+)\)(?:\s+(.*))?", 4),
            (r"^\(([ivxlcdm]+)\)(?:\s+(.*))?", 5),
            (r"^([a-z])\.(?:\s+(.*))?", 3),
            (r"^([A-Z])\.(?:\s+(.*))?", 4),
            (r"^([ivxlcdm]+)\.(?:\s+(.*))?", 5),
            (r"^[;,]\s*\(([a-z])\)(?:\s+(.*))?", 3),
            (r"^[;,]\s*\((\d+)\)(?:\s+(.*))?", 4),
        ]
        for pattern, level in clause_patterns:
            match = re.match(pattern, text, re.IGNORECASE if "ivx" in pattern else 0)
            if match:
                clause_id = match.group(1)
                clause_id = f"({clause_id})" if "(" in pattern else f"{clause_id}."
                clause_text = match.group(2).strip() if match.group(2) else ""
                if self._is_likely_clause(text, clause_text):
                    return (clause_id, clause_text, level)
        return None

    def _is_likely_clause(self, full_text: str, clause_text: str) -> bool:
        if len(full_text) > 500:
            return False
        section_words = ["section", "article", "chapter", "part"]
        return not any(word in full_text.lower()[:50] for word in section_words)


class MainTitleClassifierV6(AbstractElementwiseProcessingStep):
    def __init__(self, types_to_process=None) -> None:
        super().__init__(types_to_process=types_to_process or {NotYetClassifiedElement, TextElement})
        self.title_found = False

    def _process_element(self, element: AbstractSemanticElement, context: ElementProcessingContext) -> AbstractSemanticElement:
        if not element.html_tag or self.title_found:
            return element
        text_content = element.html_tag.text.strip()
        if self._is_likely_metadata(text_content):
            return element
        if self._is_main_title(text_content, element.html_tag):
            self.title_found = True
            return AgreementTitleElement(element.html_tag)
        return element

    def _is_likely_metadata(self, text: str) -> bool:
        metadata_patterns = [r"^Exhibit\s+\d+", r"^EX-\d+", r"^Page\s+\d+", r"^\d+$", r"^-\s*\d+\s*-$"]
        return any(re.match(pattern, text.strip(), re.IGNORECASE) for pattern in metadata_patterns)

    def _is_main_title(self, text: str, html_tag: HtmlTag) -> bool:
        if len(text.split()) > 15 or len(text) < 5:
            return False
        title_keywords = [
            "agreement",
            "contract",
            "note",
            "license",
            "lease",
            "amendment",
            "guaranty",
            "warranty",
            "deed",
            "indenture",
            "memorandum",
            "certificate",
            "letter",
            "terms",
        ]
        text_lower = text.lower()
        has_keyword = any(keyword in text_lower for keyword in title_keywords)
        if not has_keyword:
            return False
        metrics = html_tag.get_text_styles_metrics()
        bold_pct = metrics.get(("font-weight", "bold"), 0)
        center_pct = metrics.get(("text-align", "center"), 0)
        is_centered = center_pct > 0.8
        is_bold = bold_pct > 0.8
        if is_centered and is_bold:
            return True
        if is_centered or is_bold:
            strong_patterns = [
                r"^[A-Z][A-Z\s\-]+(?:AGREEMENT|CONTRACT|NOTE)$",
                r"^(?:AMENDED\s+AND\s+RESTATED\s+)?[A-Z\s]+AGREEMENT$",
                r"(?:AGREEMENT|CONTRACT|NOTE)$",
            ]
            if any(re.match(pattern, text.strip(), re.IGNORECASE) for pattern in strong_patterns):
                return True
        return bool(text[0].isupper() and has_keyword and len(text.split()) <= 8)


class LegalContentClassifierV6(AbstractElementwiseProcessingStep):
    def __init__(self, types_to_process=None) -> None:
        super().__init__(types_to_process=types_to_process or {NotYetClassifiedElement, TextElement})

    def _process_element(self, element: AbstractSemanticElement, context: ElementProcessingContext) -> AbstractSemanticElement:
        if not element.html_tag:
            return element
        text_content = element.html_tag.text.strip()
        if self._is_recital(text_content):
            return RecitalElement(element.html_tag)
        definition_info = self._extract_definition(text_content)
        if definition_info:
            term, definition = definition_info
            return DefinitionElement(element.html_tag, term=term, definition=definition)
        party_info = self._extract_party(text_content)
        if party_info:
            party_name, party_type = party_info
            return PartyElement(element.html_tag, party_name=party_name, party_type=party_type)
        exhibit_info = self._extract_exhibit_reference(text_content)
        if exhibit_info:
            exhibit_id, exhibit_title = exhibit_info
            return ExhibitElement(element.html_tag, exhibit_id=exhibit_id, exhibit_title=exhibit_title)
        return element

    def _is_recital(self, text: str) -> bool:
        recital_patterns = [r"^WHEREAS[,:]?\s", r"^Whereas[,:]?\s", r"^NOW,?\s+THEREFORE", r"^WITNESSETH[,:]?\s"]
        return any(re.match(pattern, text.strip()) for pattern in recital_patterns)

    def _extract_definition(self, text: str) -> tuple[str, str] | None:
        patterns = [
            r'"([^"]+)"\s+(?:means?|shall\s+mean)\s+(.+)',
            r'"([^"]+)"\s+(?:has\s+the\s+meaning|shall\s+have\s+the\s+meaning)\s+(.+)',
            r'(?:the\s+)?term\s+"([^"]+)"\s+(?:means?|refers?\s+to)\s+(.+)',
            r'"([^"]+)"\s*\((?:as\s+)?defined\s+(?:herein|below|above)\)',
            r'"([^"]+)"\s*\((?:the\s+)?"[^"]+"\)',
            r"\b([A-Z][a-zA-Z\s]+?)\s+(?:means?|shall\s+mean)\s+(.+)",
        ]
        for pattern in patterns:
            match = re.search(pattern, text[:500], re.IGNORECASE)
            if match:
                term = match.group(1).strip()
                definition = match.group(2).strip() if len(match.groups()) > 1 else text
                if len(term) > 2 and len(term.split()) <= 5:
                    return (term, definition)
        return None

    def _extract_party(self, text: str) -> tuple[str, str] | None:
        patterns = [
            r"([A-Z][^,]{2,50}?),\s+a\s+([^(,]{3,40}(?:\([^)]+\))?)",
            r'"([^"]+)",\s+a\s+([^(,]{3,40})',
            r"between\s+([A-Z][^,\s]{2,40}?)\s+(?:and|AND)\s+([A-Z][^,\s]{2,40})",
            r"by\s+and\s+between\s+([A-Z][^,]{2,40}?)\s+and\s+([A-Z][^,]{2,40})",
        ]
        for pattern in patterns:
            match = re.search(pattern, text[:300])
            if match and len(match.groups()) >= 2:
                party_name = match.group(1).strip()
                party_type = match.group(2).strip()
                entity_types = [
                    "corporation",
                    "company",
                    "llc",
                    "partnership",
                    "trust",
                    "individual",
                    "bank",
                    "fund",
                    "lp",
                    "inc",
                ]
                if any(entity in party_type.lower() for entity in entity_types):
                    return (party_name, party_type)
        return None

    def _extract_exhibit_reference(self, text: str) -> tuple[str, str] | None:
        patterns = [
            r"attached\s+(?:hereto\s+)?as\s+(Exhibit|Schedule|Annex|Appendix)\s+([A-Z0-9]+)",
            r"set\s+forth\s+(?:on|in)\s+(Exhibit|Schedule|Annex|Appendix)\s+([A-Z0-9]+)",
            r"(?:See|see)\s+(Exhibit|Schedule|Annex|Appendix)\s+([A-Z0-9]+)",
            r"incorporated\s+.*\s+(Exhibit|Schedule|Annex|Appendix)\s+([A-Z0-9]+)",
        ]
        for pattern in patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                exhibit_type = match.group(1).title()
                exhibit_num = match.group(2).upper()
                return (f"{exhibit_type} {exhibit_num}", "")
        return None


class HeadingClassifierV6(AbstractElementwiseProcessingStep):
    def __init__(self, types_to_process=None) -> None:
        super().__init__(types_to_process=types_to_process or {TextElement})

    def _process_element(self, element: AbstractSemanticElement, context: ElementProcessingContext) -> AbstractSemanticElement:
        if not element.html_tag:
            return element
        text_content = element.html_tag.text.strip()
        if self._is_heading(text_content, element.html_tag):
            level = self._infer_heading_level(text_content, element.html_tag)
            return HeadingElement(element.html_tag, heading_text=text_content, level=level)
        return element

    def _is_heading(self, text: str, html_tag: HtmlTag) -> bool:
        word_count = len(text.split())
        if word_count > 12 or word_count < 1:
            return False
        if re.match(r"^\d+\.?$", text.strip()):
            return False
        metrics = html_tag.get_text_styles_metrics()
        bold_pct = metrics.get(("font-weight", "bold"), 0)
        has_style = bold_pct > 0.8
        if has_style and word_count <= 8:
            return True
        if text.isupper() and len(text) > 3 and word_count > 1:
            return True
        heading_patterns = [r"^\d+\.\d+\s+[A-Z]", r"^[A-Z][A-Za-z\s]+:$", r"^(?:ARTICLE|SECTION)\s+\d+"]
        if any(re.match(pattern, text) for pattern in heading_patterns):
            return True
        if word_count <= 5:
            heading_keywords = [
                "definitions",
                "representations",
                "warranties",
                "covenants",
                "conditions",
                "termination",
                "indemnification",
                "miscellaneous",
                "general provisions",
                "notices",
                "governing law",
                "recitals",
            ]
            text_lower = text.lower()
            if any(keyword in text_lower for keyword in heading_keywords):
                return True
        return False

    def _infer_heading_level(self, text: str, html_tag: HtmlTag) -> int:
        if text.isupper():
            return 1
        if re.match(r"^\d+\.\d+\s+", text):
            return 2
        metrics = html_tag.get_text_styles_metrics()
        bold_pct = metrics.get(("font-weight", "bold"), 0)
        if bold_pct > 0.8:
            return 2
        return 2


class MainTitleClassifierV6_Duplicate(AbstractElementwiseProcessingStep):
    """Improved main title detection for V6."""

    def __init__(self, types_to_process=None) -> None:
        super().__init__(types_to_process=types_to_process or {NotYetClassifiedElement, TextElement})
        self.title_found = False

    def _process_element(self, element: AbstractSemanticElement, context: ElementProcessingContext) -> AbstractSemanticElement:
        if not element.html_tag or self.title_found:
            return element

        text_content = element.html_tag.text.strip()

        if self._is_likely_metadata(text_content):
            return element

        if self._is_main_title(text_content, element.html_tag):
            self.title_found = True
            return AgreementTitleElement(element.html_tag)

        return element

    def _is_likely_metadata(self, text: str) -> bool:
        """Check if text is likely metadata."""
        metadata_patterns = [r"^Exhibit\s+\d+", r"^EX-\d+", r"^Page\s+\d+", r"^\d+$", r"^-\s*\d+\s*-$"]
        return any(re.match(pattern, text.strip(), re.IGNORECASE) for pattern in metadata_patterns)

    def _is_main_title(self, text: str, html_tag: HtmlTag) -> bool:
        """Enhanced main title detection."""
        if len(text.split()) > 15 or len(text) < 5:
            return False

        title_keywords = [
            "agreement",
            "contract",
            "note",
            "license",
            "lease",
            "amendment",
            "guaranty",
            "warranty",
            "deed",
            "indenture",
            "memorandum",
            "certificate",
            "letter",
            "terms",
        ]

        text_lower = text.lower()
        has_keyword = any(keyword in text_lower for keyword in title_keywords)

        if not has_keyword:
            return False

        try:
            bs4_tag = _get_bs4_tag(html_tag)
            style = ""
            if bs4_tag:
                style_attr = bs4_tag.get("style", "")
                style = str(style_attr) if style_attr else ""
            is_centered = "center" in style.lower()
            is_bold = "bold" in style.lower()

            if is_centered and is_bold:
                return True

            if is_centered or is_bold:
                strong_patterns = [
                    r"^[A-Z][A-Z\s\-]+(?:AGREEMENT|CONTRACT|NOTE)$",
                    r"^(?:AMENDED\s+AND\s+RESTATED\s+)?[A-Z\s]+AGREEMENT$",
                    r"(?:AGREEMENT|CONTRACT|NOTE)$",
                ]

                if any(re.match(pattern, text.strip(), re.IGNORECASE) for pattern in strong_patterns):
                    return True
        except:
            pass

        if text.isupper() and has_keyword:
            return True

        return bool(text[0].isupper() and has_keyword and len(text.split()) <= 8)


class ContentClassifierV6(AbstractElementwiseProcessingStep):
    def __init__(self, types_to_process=None) -> None:
        super().__init__(types_to_process=types_to_process or {TextElement})

    def _process_element(self, element: AbstractSemanticElement, context: ElementProcessingContext) -> AbstractSemanticElement:
        if not element.html_tag:
            return element
        # Don't convert structural elements to content
        if isinstance(element, (ArticleElement, SectionElement, ClauseElement)):
            return element
        text_content = element.html_tag.text.strip()
        if self._is_content_text(text_content):
            return ContentTextElement(element.html_tag)
        return element

    def _is_content_text(self, text: str) -> bool:
        if len(text) < 50:
            return False
        alpha_count = sum(1 for c in text if c.isalpha())
        if alpha_count < 20:
            return False
        return not (text.isupper() and len(text.split()) < 20)


# Update LegalAgreementParser steps list
class LegalAgreementParser(AbstractSemanticElementParser):
    def get_default_steps(self) -> list[AbstractProcessingStep]:
        return [
            EarlyMetadataRemoverStep(),
            SignatureMetadataRemover(),

            EmptyElementClassifier(types_to_process={NotYetClassifiedElement}),
            HtmlCommentRemoverStep(),
            RepeatedHeaderFooterDetector(),
            ImprovedMetadataRemoverv8(types_to_process={NotYetClassifiedElement, TextElement}),
            TitleClassifier(),
            EnhancedTOCDetector(types_to_process={NotYetClassifiedElement, TextElement, TableElement}),
            TableClassifier(types_to_process={NotYetClassifiedElement}),
            TableOfContentsClassifier(types_to_process={TableElement}),
            TableRootPromoter(),
            SmartSectionClassifierV6(types_to_process={NotYetClassifiedElement, TextElement, TableElement}),
            EnhancedClauseClassifierV6(types_to_process={NotYetClassifiedElement, TextElement}),
            FallbackTitleClassifier(),
            MainTitleClassifierV6(types_to_process={NotYetClassifiedElement, TextElement}),
            LegalContentClassifierV6(types_to_process={NotYetClassifiedElement, TextElement}),
            TextClassifier(types_to_process={NotYetClassifiedElement}),
            VisualHeadingDetector(types_to_process={TextElement}),
            ConsecutivePageNumberClassifier(),
            PageContinuationMerger(),
            HeadingClassifierV6(types_to_process={TextElement}),
            ContentClassifierV6(types_to_process={TextElement}),
            HierarchyBuilder(),
            LateMetadataRemoverStep(),
            # OrphanAttacherStep(),  # Commented out as it's not defined
            EnhancedTextElementMerger(),
        ]


def render_html(
    tree: list[TreeNode] | TreeNode | SemanticTree | list[AbstractSemanticElement],
    *,
    pretty: bool | None = True,
    ignored_types: tuple[type[AbstractSemanticElement], ...] | None = None,
    char_display_limit: int | None = None,
    verbose: bool = False,
) -> str:
    """Render semantic tree as HTML with proper structure and styling."""
    # Parse input tree types
    if isinstance(tree, TreeNode):
        root_nodes = [tree]
    elif isinstance(tree, SemanticTree):
        root_nodes = list(tree)
    elif isinstance(tree, list) and tree:
        if all(isinstance(e, AbstractSemanticElement) for e in tree):
            elements = cast("list[AbstractSemanticElement]", tree)
            root_nodes = [TreeNode(e) for e in elements]
        elif all(isinstance(e, TreeNode) for e in tree):
            root_nodes = cast("list[TreeNode]", tree)
        else:
            msg = "All elements in the tree must be of type AbstractSemanticElement or TreeNode"
            raise TypeError(msg)
    else:
        msg = "Invalid type for 'tree'. Expected TreeNode, SemanticTree, list[AbstractSemanticElement], or list[TreeNode]"
        raise TypeError(msg)

    ignored_types = ignored_types or (IrrelevantElement,)
    char_display_limit = (
        char_display_limit
        if char_display_limit and char_display_limit > 0
        else DEFAULT_CHAR_DISPLAY_LIMIT
    )

    def render_node_html(node: TreeNode, level: int = 0, prefix: str = "", is_last: bool = True) -> str:
        element = node.semantic_element
        if isinstance(element, ignored_types):
            return ""

        # Get element info
        class_name = element.__class__.__name__
        lvl = getattr(element, "level", None)
        if lvl is not None:
            class_name += f" (level={lvl})"
        else:
            class_name += " (level=N/A)"

        if verbose:
            # Additional verbose info can go here if needed
            pass

        contents = element.get_summary().strip()
        if char_display_limit is not None and len(contents) > char_display_limit:
            half_limit = (char_display_limit - 3) // 2
            contents = f"{contents[:half_limit]}...{contents[-half_limit:]}"

        # Escape HTML characters
        contents = contents.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

        # Build HTML for this node
        indent_html = prefix
        if level > 0:
            indent_html += "└── " if is_last else "├── "

        html_parts = []
        html_parts.extend((f'<div class="tree-node level-{level}">', f'  <span class="tree-indent">{indent_html}</span>', f'  <span class="element-type">{class_name}</span>'))
        if contents:
            html_parts.append(f'  <span class="element-content">: {contents}</span>')
        html_parts.append("</div>")

        # Render children
        if node.children:
            child_prefix = prefix
            if level > 0:
                child_prefix += "    " if is_last else "│   "

            for i, child in enumerate(node.children):
                if isinstance(child.semantic_element, ignored_types):
                    continue
                is_last_child = i == len(node.children) - 1
                child_html = render_node_html(child, level + 1, child_prefix, is_last_child)
                if child_html:
                    html_parts.append(child_html)

        return "\n".join(html_parts)

    # Build HTML document
    html_content = """<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>Semantic Tree Visualization</title>
    <style>
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            line-height: 1.6;
            padding: 5px;
            background-color: #f5f5f5;
            margin: 0;
        }
        .tree-container {
            background-color: white;
            padding: 30px;
            border-radius: 8px;
            box-shadow: 0 2px 10px rgba(0,0,0,0.1);
            overflow-x: auto;
        }
        .tree-node {
            font-family: 'Courier New', monospace;
            white-space: pre;
            margin: 0.5px 0;
            padding: 0.5px 0;
            transition: background-color 0.2s;
        }
        .tree-node:hover {
            background-color: #f0f8ff;
        }
        .tree-indent {
            color: #999;
        }
        .element-type {
            font-weight: bold;
            color: #0056b3;
        }
        .element-content {
            color: #333;
        }
        .level-0 .element-type {
            color: #28a745;
            font-size: 1.1em;
        }
        h1 {
            color: #333;
            margin-bottom: 5px;
        }
    </style>
</head>
<body>
    <div class="tree-container">
        <h1>Semantic Tree Visualization</h1>
"""

    # Render all root nodes
    for i, root in enumerate(root_nodes):
        if isinstance(root.semantic_element, ignored_types):
            continue
        is_last = i == len(root_nodes) - 1
        html_content += render_node_html(root, 0, "", is_last)

    html_content += """
    </div>
</body>
</html>"""

    return html_content


def save_tree_as_html(tree_html: str, filename: str | Path) -> None:
    """Save tree HTML to file."""
    with open(filename, "w", encoding="utf-8") as f:
        f.write(tree_html)


def dump_semantic_tree(elements: list[AbstractSemanticElement], filename: str) -> None:
    """Dump semantic tree elements to HTML file for debugging."""
    with open(filename, "w", encoding="utf-8") as f:
        f.write("<html><body>")
        f.writelines(f"<div>{elem!s}</div>" for elem in elements)
        f.write("</body></html>")


def generate_html_trees_for_all_versions() -> None:
    """Generate HTML tree visualizations for all 100 agreements across v8, v9, and v10 parsers."""
    from pathlib import Path

    # Import v8 and v9 parsers
    from agreement_parser_v8 import AgreementParserV8Enhanced, analyze_agreement_v8_enhanced
    from agreement_parser_v9 import AgreementParserv9Enhanced, analyze_agreement_v9_enhanced

    html_dir = Path("time_to_get_real/html_files")
    if not html_dir.exists():
        return

    html_files = sorted(html_dir.glob("*.html"))

    # Create output directories
    for version in ["v8", "v9", "v10"]:
        output_dir = Path(f"semantic_trees_{version}")
        output_dir.mkdir(exist_ok=True)

    # Initialize parsers
    v8_parser = AgreementParserV8Enhanced()
    v9_parser = AgreementParserv9Enhanced()
    v10_parser = LegalAgreementParser()

    # Process each agreement
    for i, html_file in enumerate(html_files, 1):
        html_content = html_file.read_text()

        # Parse and generate HTML for v8
        try:
            v8_result = analyze_agreement_v8_enhanced(v8_parser, html_content, i)
            if v8_result.get("tree"):
                # v8 might not have SemanticTree, so handle differently
                tree_html = render_html(v8_result["tree"], char_display_limit=200)
                save_tree_as_html(tree_html, Path(f"semantic_trees_v8/tree_v8_agreement_{i:03d}.html"))
        except Exception:
            pass

        # Parse and generate HTML for v9
        try:
            v9_result = analyze_agreement_v9_enhanced(v9_parser, html_content, i)
            if v9_result.get("tree"):
                tree_html = render_html(v9_result["tree"], char_display_limit=200)
                save_tree_as_html(tree_html, Path(f"semantic_trees_v9/tree_v9_agreement_{i:03d}.html"))
        except Exception:
            pass

        # Parse and generate HTML for v10
        try:
            v10_result = analyze_agreement_v10(v10_parser, html_content, i)
            if "tree_html" in v10_result:
                save_tree_as_html(v10_result["tree_html"], Path(f"semantic_trees_v10/tree_v10_agreement_{i:03d}.html"))
        except Exception:
            pass


def calculate_orphan_rate(tree) -> float:
    total_nodes = [0]
    orphans = [0]

    def traverse(node) -> None:
        total_nodes[0] += 1
        # Check if node is orphaned and has a level > 0
        if node.parent is None:
            # Check if element has level attribute (i.e., inherits from AbstractLevelElement)
            level = getattr(node.semantic_element, "level", 0)
            if level > 0:
                orphans[0] += 1
        for child in node.children:
            traverse(child)
    for root in tree._root_nodes:
        traverse(root)
    return (orphans[0] / total_nodes[0] * 100) if total_nodes[0] else 0.0


def analyze_agreement_v10(parser: LegalAgreementParser, html_content: str, agreement_num: int) -> dict[str, Any]:
    try:
        global _GLOBAL_IS_WDESK
        _GLOBAL_IS_WDESK = "<!-- Document created using Wdesk -->" in html_content
        elements = parser.parse(html_content)

        # Since parse now returns flat list with ids from tree, but for analysis use tree
        rules: list[AbstractNestingRule] = [LegalDocumentNestingRule(), IndentationNestingRule()]
        tree_builder = TreeBuilder(get_rules=(lambda: rules))
        tree = tree_builder.build(elements)

        # Calculate metrics
        orphan_rate = calculate_orphan_rate(tree)

        # Count elements and types
        total_elements = len(elements)
        orphan_count = int(orphan_rate * total_elements / 100) if total_elements > 0 else 0
        root_count = len(tree._root_nodes)

        # Calculate hierarchy depth using both methods for comparison
        # Method 1: Tree-based depth (v10 approach)
        tree_depth = 0

        def calculate_tree_depth(node, depth=0) -> int:
            max_depth = depth
            for child in node.children:
                child_depth = calculate_tree_depth(child, depth + 1)
                max_depth = max(max_depth, child_depth)
            return max_depth

        for root in tree._root_nodes:
            root_depth = calculate_tree_depth(root)
            tree_depth = max(tree_depth, root_depth)

        # Method 2: Level-based depth (v6-v9 compatible approach)
        # Get hierarchical elements that have level attributes
        level_based_elements = [elem for elem in elements if hasattr(elem, "level") and isinstance(elem, AbstractLevelElement)]

        level_based_depth = max((e.level for e in level_based_elements), default=0) if level_based_elements else 0

        # Use level-based depth for compatibility with v6-v9, but store both
        hierarchy_depth = level_based_depth

        # Count element types
        type_counts = {}
        has_articles = False
        has_sections = False
        has_clauses = False
        has_toc = False

        for elem in elements:
            elem_type = type(elem).__name__
            type_counts[elem_type] = type_counts.get(elem_type, 0) + 1

            # Check element types by class name
            if "Article" in elem_type:
                has_articles = True
            if "Section" in elem_type:
                has_sections = True
            if "Clause" in elem_type:
                has_clauses = True
            if "TableOfContents" in elem_type or hasattr(elem, "is_table_of_contents"):
                has_toc = True

        # Get hierarchical elements (non-orphans)
        hierarchical_elements = []
        relevant_elements = []

        def collect_hierarchical(node) -> None:
            # Node is hierarchical if it has a parent or if it's a root node with level 0
            if node.parent is not None or getattr(node.semantic_element, "level", 1) == 0:
                hierarchical_elements.append(node.semantic_element)
            relevant_elements.append(node.semantic_element)
            for child in node.children:
                collect_hierarchical(child)

        for root in tree._root_nodes:
            collect_hierarchical(root)

        # Check if document has structure
        has_structure = len(hierarchical_elements) > 0
        has_title = False
        title_text = None

        # Look for title in first few elements
        for elem in elements[:5]:
            elem_type = type(elem).__name__
            if "Title" in elem_type:
                has_title = True
                title_text = getattr(elem, "text", getattr(elem, "get_text", lambda: ""))()
                if callable(title_text):
                    title_text = title_text()
                break

        # Determine status and grade
        if total_elements == 0:
            status = "empty"
            grade = "F"
        elif orphan_rate > 50:
            status = "poor"
            grade = "D"
        elif orphan_rate > 20:
            status = "fair"
            grade = "C"
        elif orphan_rate > 10:
            status = "good"
            grade = "B"
        else:
            status = "excellent"
            grade = "A"

        # Create the semantic tree and render it
        semantic_tree = SemanticTree(tree._root_nodes)
        tree_rendering = semantic_tree.render(
            pretty=True,
            char_display_limit=200,
            verbose=False,
        )

        # Also create HTML rendering
        tree_html = render_html(semantic_tree, char_display_limit=200)

        result = {
            "agreement_num": agreement_num,
            "status": status,
            "grade": grade,
            "diagnostic_info": {},
            "title": has_title,
            "title_text": title_text,
            "structure": has_structure,
            "elements": elements,
            "relevant_elements": relevant_elements,
            "hierarchical_elements": hierarchical_elements,
            "hierarchy_depth": hierarchy_depth,
            "metadata_removed": 0,
            "v8_stats": {},
            "type_counts": type_counts,
            "has_articles": has_articles,
            "has_sections": has_sections,
            "has_clauses": has_clauses,
            "has_toc": has_toc,
            "total_elements": total_elements,
            "relevant_count": len(relevant_elements),
            "orphan_rate": orphan_rate,
            "orphan_count": orphan_count,
            "root_count": root_count,
            "tree": tree,
            "tree_rendering": tree_rendering,
            "tree_html": tree_html,
        }

        # Add NO_STRUCTURE flag if needed
        if len(hierarchical_elements) == 0:
            result["flags"] = ["NO_STRUCTURE"]

        return result

    except Exception as e:
        import traceback
        error_msg = f"{e!s}\n{traceback.format_exc()}"
        return {
            "agreement_num": agreement_num,
            "error": str(e),
            "error_trace": error_msg,
            "status": "error",
            "grade": "F",
            "total_elements": 0,
            "orphan_rate": 100.0,
            "orphan_count": 0,
            "root_count": 0,
        }


# Comprehensive test adapted
def comprehensive_test_v10_enhanced_with_regression_check(
    v8_results: list[dict] | None = None,
    v9_results: list[dict] | None = None,
    enable_tracing: bool = True,
    save_traces: bool = False,
    render_html_trees: bool = False,
) -> None:
    """Test v10 parser on all agreements with regression checks against v8 and v9, and optional tracing.

    Enhancements:
    - Parallel parsing for v8, v9, v10 if results not provided
    - Detailed delta analysis (orphan rates, structure depth, element counts)
    - CSV export for all metrics across versions
    - Visual tree dumping for failures
    - Adaptive tolerance based on document complexity
    - Comprehensive stats including parse time
    """
    import csv
    import time
    from pathlib import Path

    enable_tracing = True
    save_traces = True

    html_dir = Path("time_to_get_real/html_files")
    if not html_dir.exists():
        return

    html_files = sorted(html_dir.glob("*.html"))

    results_v10 = []
    regression_failures = []
    all_traces = {}
    parse_times = defaultdict(list)  # version: [times]
    from agreement_parser_v8 import AgreementParserV8Enhanced, analyze_agreement_v8_enhanced
    from agreement_parser_v9 import AgreementParserv9Enhanced, analyze_agreement_v9_enhanced
    # Load or generate v8/v9 results if not provided
    if v8_results is None:
        v8_results = []
        v8_parser = AgreementParserV8Enhanced()  # Assume v8 parser class exists
    if v9_results is None:
        v9_results = []
        v9_parser = AgreementParserv9Enhanced()

    for i, html_file in enumerate(html_files, 1):
        html_content = html_file.read_text()

        # Parse with v10
        start_time = time.time()
        v10_parser = LegalAgreementParser()
        v10_result = analyze_agreement_v10(v10_parser, html_content, i)
        parse_times["v10"].append(time.time() - start_time)
        results_v10.append(v10_result)

        # Get/Parse v9
        if v9_results and i <= len(v9_results):
            v9_result = v9_results[i - 1]
        else:
            start_time = time.time()
            v9_result = analyze_agreement_v9_enhanced(v9_parser, html_content, i)
            parse_times["v9"].append(time.time() - start_time)
            v9_results.append(v9_result)

        # Get/Parse v8
        if v8_results and i <= len(v8_results):
            v8_result = v8_results[i - 1]
        else:
            start_time = time.time()
            v8_result = analyze_agreement_v8_enhanced(v8_parser, html_content, i)  # Assume v8 analysis function
            parse_times["v8"].append(time.time() - start_time)
            v8_results.append(v8_result)

        # Collect traces for v10
        if enable_tracing:
            traces = get_step_traces(v10_parser)
            all_traces[i] = traces

        # Regression checks
        v10_orphan = v10_result.get("orphan_rate", 100.0)
        v9_orphan = v9_result.get("orphan_rate", 100.0)
        v8_orphan = v8_result.get("orphan_rate", 100.0)

        # Skip regression checks if there's an error
        if "error" in v10_result:
            if "error_trace" in v10_result and i == 1:  # Print trace for first error only
                pass
            continue

        # Adaptive tolerance: higher for complex docs (more elements)
        tolerance = 0.5 if v10_result.get("total_elements", 0) < 500 else 1.0

        if v10_orphan > v9_orphan + tolerance:
            regression_failures.append({
                "agreement": i,
                "type": "v10 vs v9",
                "v9_rate": v9_orphan,
                "v10_rate": v10_orphan,
                "delta": v10_orphan - v9_orphan,
                "tolerance": tolerance,
            })
        if v9_orphan > v8_orphan + tolerance:
            regression_failures.append({
                "agreement": i,
                "type": "v9 vs v8",
                "v8_rate": v8_orphan,
                "v9_rate": v9_orphan,
                "delta": v9_orphan - v8_orphan,
                "tolerance": tolerance,
            })

        # Compare structure depths
        v10_depth = v10_result.get("hierarchy_depth", 0)
        v9_depth = v9_result.get("hierarchy_depth", 0)
        v8_depth = v8_result.get("hierarchy_depth", 0)
        if v10_depth < v9_depth - 1 or v10_depth < v8_depth - 1:
            regression_failures.append({
                "agreement": i,
                "type": "depth regression",
                "v8_depth": v8_depth,
                "v9_depth": v9_depth,
                "v10_depth": v10_depth,
            })

        # Element count comparisons (e.g., sections)
        for elem_type in ["SectionElement", "ClauseElement", "ArticleElement"]:
            v10_count = v10_result.get("type_counts", {}).get(elem_type, 0)
            v9_count = v9_result.get("type_counts", {}).get(elem_type, 0)
            if v10_count < v9_count * 0.8:  # 20% drop threshold
                regression_failures.append({
                    "agreement": i,
                    "type": f"{elem_type} count drop",
                    "v9_count": v9_count,
                    "v10_count": v10_count,
                })

        # Save tree visualizations as HTML for ALL agreements
        if "tree_html" in v10_result:
            # Create output directory if it doesn't exist
            output_dir = Path("semantic_trees_v10")
            output_dir.mkdir(exist_ok=True)

            save_tree_as_html(v10_result["tree_html"], output_dir / f"tree_v10_agreement_{i:03d}.html")
            if i <= 5:
                pass

            # Print first tree sample in terminal
            if i == 1:
                tree_lines = v10_result["tree_rendering"].split("\n")[:30]  # First 30 lines
                for _line in tree_lines:
                    pass
                if len(v10_result["tree_rendering"].split("\n")) > 30:
                    pass

        # Dump tree on failure
        if ("FAILED" in v10_result.get("status", "") or "error" in v10_result) and "elements" in v10_result:
            dump_semantic_tree(v10_result["elements"], f"failure_tree_agreement_{i}.html")

        # Trace summary
        if enable_tracing and i in all_traces:
            trace_lines = format_trace_summary(all_traces[i], i)
            if trace_lines:
                for _line in trace_lines:
                    pass

    # Consolidated metrics across versions
    versions = ["v8", "v9", "v10"]
    for version, res_list in zip(versions, [v8_results, v9_results, results_v10]):
        if res_list and len(res_list) > 0:
            sum(r.get("orphan_rate", 0) for r in res_list) / len(res_list)
            sum(r.get("hierarchy_depth", 0) for r in res_list) / len(res_list)
            sum(r.get("type_counts", {}).get("SectionElement", 0) for r in res_list)
            sum(parse_times[version]) / len(parse_times[version]) if parse_times[version] else 0

    # Regression summary
    if regression_failures:
        for _failure in regression_failures:
            pass

    # Export metrics to CSV
    with open("version_comparison_metrics.csv", "w", newline="", encoding="utf-8") as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=["agreement", "version", "orphan_rate", "hierarchy_depth", "parse_time", "section_count", "clause_count"])
        writer.writeheader()
        for i in range(1, len(html_files) + 1):
            for version, res_list in zip(versions, [v8_results, v9_results, results_v10]):
                if i <= len(res_list):
                    res = res_list[i - 1]
                    writer.writerow({
                        "agreement": i,
                        "version": version,
                        "orphan_rate": res.get("orphan_rate", 0),
                        "hierarchy_depth": res.get("hierarchy_depth", 0),
                        "parse_time": parse_times[version][i - 1],
                        "section_count": res["type_counts"].get("SectionElement", 0),
                        "clause_count": res["type_counts"].get("ClauseElement", 0),
                    })

    # Trace reports
    if enable_tracing and all_traces:
        aggregate_orphan_metrics(all_traces)

        step_impact = defaultdict(lambda: {"orphans_reduced": 0, "roots_added": 0, "count": 0})
        for traces in all_traces.values():
            for step, delta in traces.items():
                if delta.get("Δorphans", 0) < 0:
                    step_impact[step]["orphans_reduced"] += -delta["Δorphans"]
                    step_impact[step]["count"] += 1
                if delta.get("Δroots", 0) > 0:
                    step_impact[step]["roots_added"] += delta["Δroots"]

        sorted_steps = sorted(step_impact.items(), key=lambda x: x[1]["orphans_reduced"], reverse=True)
        for step, impact in sorted_steps[:5]:
            if impact["orphans_reduced"] > 0:
                pass

        if save_traces:
            generate_trace_report(all_traces, "v10_trace_report.txt")
            export_traces_to_csv(all_traces, "v10_trace_metrics.csv")

    # Overall comparison
    if v8_results and v9_results:
        sum(v8["orphan_rate"] - v9["orphan_rate"] for v8, v9 in zip(v8_results, v9_results)) / len(v8_results)
        sum(v9["orphan_rate"] - v10["orphan_rate"] for v9, v10 in zip(v9_results, results_v10)) / len(v9_results)

        sum(v9["hierarchy_depth"] - v8["hierarchy_depth"] for v8, v9 in zip(v8_results, v9_results)) / len(v8_results)
        sum(v10["hierarchy_depth"] - v9["hierarchy_depth"] for v9, v10 in zip(v9_results, results_v10)) / len(v9_results)

        sum(parse_times["v10"]) / sum(parse_times["v9"]) if sum(parse_times["v9"]) > 0 else 1

    # Summary of HTML tree files generated


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Agreement Parser v10")
    parser.add_argument("html_file", nargs="?", help="HTML file to parse")
    parser.add_argument("--dump-tree", action="store_true", help="Dump intermediate trees to HTML files")
    parser.add_argument("--test", action="store_true", help="Run comprehensive test suite")
    parser.add_argument("--render", action="store_true", help="Generate HTML visualizations for all 3 parser versions")
    args = parser.parse_args()

    if args.render:
        generate_html_trees_for_all_versions()
    elif args.test or not args.html_file:
        comprehensive_test_v10_enhanced_with_regression_check(enable_tracing=True, save_traces=True)
    else:
        html_file = Path(args.html_file)
        if not html_file.exists():
            sys.exit(1)
        html_content = html_file.read_text()
        parser_inst = LegalAgreementParser()
        elements = parser_inst.parse(html_content)
        if args.dump_tree:
            dump_semantic_tree(elements, "final_tree.html")
        result = analyze_agreement_v10(parser_inst, html_content, 1)
