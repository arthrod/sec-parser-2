#!/usr/bin/env python3
"""Legal Agreement Parser v10 - Full sec-parser integration for precision hierarchy extraction."""
from __future__ import annotations

from step_tracer import activate_tracing

activate_tracing()

import re
import sys

from sec_parser import (
    AbstractNestingRule,
    AbstractSemanticElement,
    HtmlTag,
    NotYetClassifiedElement,
    TableElement,
    TextElement,
    TitleElement,
    TreeBuilder,
)
from sec_parser.processing_engine.core import AbstractSemanticElementParser
from sec_parser.processing_steps import (
    AbstractElementwiseProcessingStep,
    AbstractProcessingStep,
    EmptyElementClassifier,
    HighlightedTextClassifier,
    IndividualSemanticElementExtractor,
    IntroductorySectionElementClassifier,
    PageHeaderClassifier,
    PageNumberClassifier,
    SupplementaryTextClassifier,
    TableClassifier,
    TableOfContentsClassifier,
    TextClassifier,
    TextElementMerger,
    TopSectionManagerFor10Q,
)
from sec_parser.processing_steps.abstract_classes.abstract_element_batch_processing_step import (
    AbstractElementBatchProcessingStep,
)
from sec_parser.semantic_elements.abstract_semantic_element import AbstractLevelElement
from sec_parser.semantic_elements.table_element.table_of_contents_element import TableOfContentsElement

# ========================================================================
# CUSTOM SEMANTIC ELEMENTS (sec-parser based)
# ========================================================================


class AgreementTitleElement(TitleElement):
    """Main agreement title with hierarchy support."""
    def __init__(self, html_tag: HtmlTag, level: int = 0, **kwargs) -> None:
        super().__init__(html_tag, level=level, **kwargs)


class ArticleElement(TitleElement):
    """Article-level sections with enhanced structure."""
    def __init__(self, html_tag: HtmlTag, article_number: str = "", article_title: str = "", **kwargs) -> None:
        super().__init__(html_tag, level=1, **kwargs)
        self.article_number = article_number
        self.article_title = article_title

    def normalized_id(self) -> str | None:
        if self.article_number:
            match = re.search(r"([IVX]+|\d+)", self.article_number)
            if match:
                return f"article_{match.group(1).lower()}"
        return None


class SectionElement(TitleElement):
    """Numbered sections with enhanced hierarchy."""
    def __init__(self, html_tag: HtmlTag, section_number: str = "", section_title: str = "", **kwargs) -> None:
        super().__init__(html_tag, level=2, **kwargs)
        self.section_number = self._normalize_section_number(section_number)
        self.section_title = section_title

    def _normalize_section_number(self, number: str) -> str:
        if re.match(r"^\d+(?:\.\d+)*$", number.strip()):
            return number.strip()
        return number

    def normalized_id(self) -> str | None:
        if self.section_number:
            match = re.search(r"(\d+(?:\.\d+)*)", self.section_number)
            if match:
                return match.group(1)
        return None


class ClauseElement(AbstractLevelElement):
    """Clauses with enhanced hierarchy and cross-reference support."""
    def __init__(self, html_tag: HtmlTag, clause_id: str = "", clause_text: str = "", level: int = 3, **kwargs) -> None:
        super().__init__(html_tag, level=level, **kwargs)
        self.clause_id = clause_id
        self.clause_text = clause_text

    def normalized_id(self) -> str | None:
        if self.clause_id:
            match = re.search(r"\(?([a-zA-Z0-9]+)\)?", self.clause_id)
            if match:
                return match.group(1).lower()
        return None


class DefinitionElement(AbstractLevelElement):
    """Definitions with hierarchy."""
    def __init__(self, html_tag: HtmlTag, term: str = "", definition: str = "", level: int = 3, **kwargs) -> None:
        super().__init__(html_tag, level=level, **kwargs)
        self.term = term
        self.definition = definition

    def normalized_id(self) -> str | None:
        if self.term:
            cleaned = re.sub(r"[^a-zA-Z0-9]", "_", self.term.lower())
            return f"def_{cleaned}"
        return None


class PartyElement(AbstractLevelElement):
    """Contract parties with hierarchy."""
    def __init__(self, html_tag: HtmlTag, party_name: str = "", party_type: str = "", level: int = 2, **kwargs) -> None:
        super().__init__(html_tag, level=level, **kwargs)
        self.party_name = party_name
        self.party_type = party_type

    def normalized_id(self) -> str | None:
        if self.party_name:
            cleaned = re.sub(r"[^a-zA-Z0-9]", "_", self.party_name.lower())
            return f"party_{cleaned}"
        return None


class SignatureBlockElement(AbstractLevelElement):
    """Signature blocks with hierarchy."""
    def __init__(self, html_tag: HtmlTag, level: int = 2, **kwargs) -> None:
        super().__init__(html_tag, level=level, **kwargs)


class ExhibitElement(TitleElement):
    """Exhibits and attachments with hierarchy."""
    def __init__(self, html_tag: HtmlTag, exhibit_id: str = "", exhibit_title: str = "", **kwargs) -> None:
        super().__init__(html_tag, level=1, **kwargs)
        self.exhibit_id = exhibit_id
        self.exhibit_title = exhibit_title

    def normalized_id(self) -> str | None:
        if self.exhibit_id:
            match = re.search(r"([A-Z0-9]+)$", self.exhibit_id)
            if match:
                return f"exhibit_{match.group(1).lower()}"
        return None

# ========================================================================
# LEGAL NESTING RULES (sec-parser based)
# ========================================================================


class LegalNestingRule(AbstractNestingRule):
    """Define hierarchy rules for legal documents using sec-parser NestingRule pattern."""
    def should_be_nested(self, parent: AbstractSemanticElement, child: AbstractSemanticElement) -> bool:
        # Article can contain Sections
        if isinstance(parent, ArticleElement) and isinstance(child, SectionElement):
            return True

        # Section can contain Clauses and Text
        if isinstance(parent, SectionElement) and isinstance(child, (ClauseElement, TextElement, DefinitionElement)):
            return True

        # Clause can contain Text
        if isinstance(parent, ClauseElement) and isinstance(child, TextElement):
            return True

        # Table of Contents can contain Sections
        if isinstance(parent, TableOfContentsElement) and isinstance(child, (SectionElement, TitleElement)):
            return True

        # Definition sections can contain DefinitionElements
        return bool(isinstance(parent, TitleElement) and "definition" in parent.text.lower() and isinstance(child, DefinitionElement))

# ========================================================================
# CUSTOM PROCESSING STEPS (sec-parser compatible)
# ========================================================================


class LegalTitleClassifier(AbstractElementBatchProcessingStep):
    """Extend sec-parser's TitleClassifier for legal documents with pattern-based classification."""
    _ARTICLE_PATTERN = re.compile(r"^ARTICLE\s+([IVX]+|\d+)(?:\s*[-–—.]\s*(.*))?", re.IGNORECASE)
    _SECTION_PATTERN = re.compile(
        r"^(?P<num>\d+(?:\.\d+)*)[\.\)]?\s*[-–—]?\s+(?P<title>(?!continued\b|page\b|table\b)[^.;]{3,100})$",
        re.IGNORECASE,
    )

    def _process_elements(self, elements: list[AbstractSemanticElement], context) -> list[AbstractSemanticElement]:
        for i, element in enumerate(elements):
            if not isinstance(element, TitleElement) or not element.html_tag:
                continue

            text = element.text.strip()

            # Classify Articles
            article_match = self._ARTICLE_PATTERN.match(text)
            if article_match:
                elements[i] = ArticleElement(
                    element.html_tag,
                    article_number=article_match.group(1),
                    article_title=article_match.group(2) or text,
                )
                continue

            # Classify Sections
            section_match = self._SECTION_PATTERN.match(text)
            if section_match:
                elements[i] = SectionElement(
                    element.html_tag,
                    section_number=section_match.group("num"),
                    section_title=section_match.group("title").strip(),
                )

        return elements


class VisualHeadingDetector(AbstractElementwiseProcessingStep):
    """Precision heading detection using sec-parser's style metrics."""
    _SECTION_RE = re.compile(
        r"^(?P<num>\d+(?:\.\d+)*)[\.\)]?\s*[-–—]?\s+(?P<title>(?!continued\b|page\b|table\b)[^.;]{3,100})$",
        re.IGNORECASE,
    )
    _ARTICLE_RE = re.compile(
        r"^ARTICLE\s+(?P<num>[IVXLCDM]+|\d+)(?:\s*[-–—.]?\s*(?P<title>[^.;]{3,80}))?$",
        re.IGNORECASE,
    )

    def _process_element(self, element: AbstractSemanticElement, context) -> AbstractSemanticElement:
        # Skip non-text elements
        if not isinstance(element, TextElement) or not element.html_tag:
            return element

        # Use sec-parser's style metrics instead of custom CSS parsing
        style_metrics = element.html_tag.get_text_styles_metrics()

        # Check for visual heading characteristics
        bold_pct = style_metrics.get(("font-weight", "bold"), 0)
        size_pt = style_metrics.get("font-size", 0)
        is_centered = style_metrics.get(("text-align", "center"), 0) > 0.5

        # Thresholds for heading detection
        if not (bold_pct > 0.7 and (size_pt > 11 or is_centered)):
            return element

        text = element.text.strip()

        # ARTICLE detection
        article_match = self._ARTICLE_RE.match(text)
        if article_match:
            return ArticleElement(
                element.html_tag,
                article_number=article_match.group("num"),
                article_title=article_match.group("title") or text,
            )

        # SECTION detection
        section_match = self._SECTION_RE.match(text)
        if section_match:
            return SectionElement(
                element.html_tag,
                section_number=section_match.group("num"),
                section_title=section_match.group("title").strip(),
            )

        # Generic heading
        return TitleElement(element.html_tag, level=3)


class LegalContentClassifier(AbstractElementwiseProcessingStep):
    """Precision classification of legal content types using sec-parser patterns."""
    _DEFINITION_PATTERNS = [
        r'"([^"]+)"\s+(?:means?|shall\s+mean)\s+(.+)',
        r'term\s+"([^"]+)"\s+(?:means?|refers?\s+to)\s+(.+)',
    ]
    _PARTY_PATTERNS = [
        r"([A-Z][^,]{2,50}?),\s+a\s+([^(,]{3,40}(?:\([^)]+\))?)",
        r"between\s+([A-Z][^,\s]{2,40}?)\s+(?:and|AND)\s+([A-Z][^,\s]{2,40})",
    ]
    _EXHIBIT_PATTERNS = [
        r"attached\s+(?:hereto\s+)?as\s+(Exhibit|Schedule|Annex|Appendix)\s+([A-Z0-9]+)",
        r"incorporated\s+.*\s+(Exhibit|Schedule|Annex|Appendix)\s+([A-Z0-9]+)",
    ]

    def _process_element(self, element: AbstractSemanticElement, context) -> AbstractSemanticElement:
        if not isinstance(element, TextElement) or not element.text:
            return element

        text = element.text.strip()

        # Definition extraction
        for pattern in self._DEFINITION_PATTERNS:
            match = re.search(pattern, text, re.IGNORECASE)
            if match and len(match.group(1)) > 2:
                return DefinitionElement(
                    element.html_tag,
                    term=match.group(1),
                    definition=match.group(2) if len(match.groups()) > 1 else text,
                )

        # Party identification
        for pattern in self._PARTY_PATTERNS:
            match = re.search(pattern, text, re.IGNORECASE)
            if match and len(match.groups()) >= 2:
                return PartyElement(
                    element.html_tag,
                    party_name=match.group(1),
                    party_type=match.group(2),
                )

        # Exhibit reference
        for pattern in self._EXHIBIT_PATTERNS:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                return ExhibitElement(
                    element.html_tag,
                    exhibit_id=f"{match.group(1)} {match.group(2)}",
                    exhibit_title="",
                )

        # Signature block detection
        if self._is_signature_block(text):
            return SignatureBlockElement(element.html_tag)

        return element

    def _is_signature_block(self, text: str) -> bool:
        signature_keywords = ["signature", "signed", "by:", "name:", "title:", "date:"]
        return sum(keyword in text.lower() for keyword in signature_keywords) >= 3


class ClauseClassifier(AbstractElementwiseProcessingStep):
    """Precision clause detection using hierarchical patterns."""
    _CLAUSE_PATTERNS = [
        (r"^\(([a-z])\)\s+(.*)", 3),    # (a) Clause text
        (r"^\(([A-Z])\)\s+(.*)", 4),    # (A) Clause text
        (r"^\((\d+)\)\s+(.*)", 4),      # (1) Clause text
        (r"^([a-z])\.\s+(.*)", 3),      # a. Clause text
        (r"^([A-Z])\.\s+(.*)", 4),      # A. Clause text
        (r"^(\d+)\.\s+(.*)", 4),        # 1. Clause text
    ]

    def _process_element(self, element: AbstractSemanticElement, context) -> AbstractSemanticElement:
        if not isinstance(element, TextElement) or not element.text:
            return element

        text = element.text.strip()

        for pattern, level in self._CLAUSE_PATTERNS:
            match = re.match(pattern, text)
            if match:
                return ClauseElement(
                    element.html_tag,
                    clause_id=match.group(1),
                    clause_text=match.group(2),
                    level=level,
                )

        return element


class EnhancedTOCDetector(AbstractElementwiseProcessingStep):
    """Precision TOC detection integrated with sec-parser's TableOfContentsElement."""
    _TOC_HEADER_PATTERNS = [
        r"^TABLE\s+OF\s+CONTENTS?$",
        r"^Contents?$",
        r"^INDEX$",
    ]

    def __init__(self) -> None:
        super().__init__()
        self.in_toc_section = False

    def _process_element(self, element: AbstractSemanticElement, context) -> AbstractSemanticElement:
        if not element.html_tag:
            return element

        # Table-based TOC detection
        if isinstance(element, TableElement) and self._is_toc_table(element):
            return TableOfContentsElement(element.html_tag)

        text = element.text.strip()

        # TOC header detection
        if any(re.match(pattern, text, re.IGNORECASE) for pattern in self._TOC_HEADER_PATTERNS):
            self.in_toc_section = True
            return TableOfContentsElement(element.html_tag)

        # TOC content detection
        if self.in_toc_section and isinstance(element, TextElement):
            section_info = self._extract_toc_section(text)
            if section_info:
                return SectionElement(
                    element.html_tag,
                    section_number=section_info[0],
                    section_title=section_info[1],
                    level=1,
                )

        return element

    def _is_toc_table(self, element: TableElement) -> bool:
        if not element.html_tag:
            return False
        text = element.html_tag.clone_with_text().lower()
        toc_indicators = ["table of contents", "contents", "index", "page"]
        return sum(indicator in text for indicator in toc_indicators) >= 2

    def _extract_toc_section(self, text: str) -> tuple[str, str] | None:
        patterns = [
            r"^(\d+(?:\.\d+)*)\.\s+([^.\s].+?)(?:\.{3,}|\s{5,})\d+$",
            r"^Section\s+(\d+(?:\.\d+)*)\s*[-–—]\s*([^.\s].+?)\s+\d+$",
        ]
        for pattern in patterns:
            match = re.match(pattern, text)
            if match:
                return match.group(1), match.group(2).strip()
        return None

# ========================================================================
# LEGAL DOCUMENT PARSER (sec-parser based)
# ========================================================================


class LegalDocumentParser(AbstractSemanticElementParser):
    """Precision legal document parser using sec-parser architecture."""
    def get_default_steps(self) -> list[AbstractProcessingStep]:
        return [
            # Initial extraction and cleanup
            IndividualSemanticElementExtractor(),
            EmptyElementClassifier(types_to_process={NotYetClassifiedElement}),

            # Table processing
            TableClassifier(),
            TableOfContentsClassifier(),
            EnhancedTOCDetector(),

            # Title classification
            TextClassifier(),
            LegalTitleClassifier(),
            VisualHeadingDetector(),

            # Content classification
            LegalContentClassifier(),
            ClauseClassifier(),

            # Structural enhancements
            TopSectionManagerFor10Q(),
            IntroductorySectionElementClassifier(),

            # Text processing
            HighlightedTextClassifier(),
            SupplementaryTextClassifier(),
            PageHeaderClassifier(),
            PageNumberClassifier(),

            # Final processing
            TextElementMerger(),
        ]

    def parse(self, html: str) -> list[AbstractSemanticElement]:
        """Parse document and build semantic hierarchy."""
        elements = super().parse(html)

        # Build precision hierarchy using sec-parser's TreeBuilder
        tree_builder = TreeBuilder([LegalNestingRule()])
        tree = tree_builder.build(elements)

        # Return root nodes of the semantic tree
        return tree._root_nodes

# ========================================================================
# ANALYSIS AND DIAGNOSTICS
# ========================================================================


def analyze_legal_document(html: str) -> dict:
    """Analyze legal document with precision hierarchy extraction."""
    parser = LegalDocumentParser()
    elements = parser.parse(html)

    # Extract hierarchical structure
    hierarchy = extract_hierarchy(elements)

    # Calculate precision metrics
    stats = calculate_precision_metrics(elements)

    return {
        "hierarchy": hierarchy,
        "stats": stats,
        "elements": elements,
    }


def extract_hierarchy(elements: list[AbstractSemanticElement]) -> list:
    """Extract hierarchical structure from semantic elements."""

    def traverse(element: AbstractSemanticElement, level: int = 0) -> dict:
        node = {
            "type": type(element).__name__,
            "level": getattr(element, "level", 0),
            "text": element.text[:100] + "..." if len(element.text) > 100 else element.text,
        }

        # Add element-specific details
        if isinstance(element, ArticleElement):
            node["article_number"] = element.article_number
            node["article_title"] = element.article_title
        elif isinstance(element, SectionElement):
            node["section_number"] = element.section_number
            node["section_title"] = element.section_title
        elif isinstance(element, ClauseElement):
            node["clause_id"] = element.clause_id
            node["clause_text"] = element.clause_text
        elif isinstance(element, DefinitionElement):
            node["term"] = element.term
            node["definition"] = element.definition
        elif isinstance(element, PartyElement):
            node["party_name"] = element.party_name
            node["party_type"] = element.party_type

        # Recursively process children
        if hasattr(element, "children"):
            node["children"] = [traverse(child, level + 1) for child in element.children]

        return node

    return [traverse(element) for element in elements]


def calculate_precision_metrics(elements: list[AbstractSemanticElement]) -> dict:
    """Calculate precision metrics for hierarchy extraction."""
    # Count element types
    type_counts = {}
    for element in elements:
        type_name = type(element).__name__
        type_counts[type_name] = type_counts.get(type_name, 0) + 1

    # Calculate hierarchy metrics
    max_depth = 0
    elements_with_parent = 0
    for element in elements:
        if hasattr(element, "parent"):
            elements_with_parent += 1
        if hasattr(element, "level") and element.level > max_depth:
            max_depth = element.level

    return {
        "element_types": type_counts,
        "hierarchy_depth": max_depth,
        "structured_elements": elements_with_parent,
        "structured_percentage": elements_with_parent / len(elements) * 100 if elements else 0,
    }


if __name__ == "__main__":
    # Example usage
    if len(sys.argv) < 2:
        sys.exit(1)

    with open(sys.argv[1], encoding="utf-8") as f:
        html_content = f.read()

    analysis = analyze_legal_document(html_content)

    # Print hierarchy summary
    print_hierarchy(analysis["hierarchy"])

    # Print precision metrics


def print_hierarchy(hierarchy: list, indent: int = 0) -> None:
    """Print document hierarchy with proper indentation."""
    for node in hierarchy:
        "  " * indent
        if "children" in node:
            print_hierarchy(node["children"], indent + 1)
