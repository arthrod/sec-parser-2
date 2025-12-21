#!/usr/bin/env python3
"""
Generate visual semantic trees using the new AgreementParserv9Enhanced parser.

This script processes all HTML files in time_to_get_real/html_files/ and generates
visual tree representations similar to the format in visual_semantic_trees__/ but
using the enhanced parser with all the new improvements.
"""

import sys
from pathlib import Path
from typing import List, Dict, Any

# Import the new parser
from starting_anew_parser_v3 import (
    AgreementParserv9Enhanced,
    analyze_agreement_v9_enhanced,
    HierarchicalElement,
    AgreementTitleElement,
    ArticleElement,
    SectionElement,
    ClauseElement,
    HeadingElement,
    ContentTextElement,
    DefinitionElement,
    PartyElement,
    RecitalElement,
    SignatureBlockElement,
    ExhibitElement,
    TableOfContentsElement,
    MetadataElement,
    TextElement
)
from sec_parser.semantic_elements.abstract_semantic_element import AbstractSemanticElement


def format_element_type(element: AbstractSemanticElement) -> str:
    """Format element type name for display."""
    type_name = type(element).__name__

    # Special formatting for specific types
    if isinstance(element, AgreementTitleElement):
        return "AGREEMENT"
    elif isinstance(element, ArticleElement):
        return f"Article {element.article_number}" if element.article_number else "Article"
    elif isinstance(element, SectionElement):
        return f"Section {element.section_number}" if element.section_number else "Section"
    elif isinstance(element, ClauseElement):
        return f"Clause ({element.clause_id})" if element.clause_id else "Clause"
    elif isinstance(element, HeadingElement):
        return f"Heading (L{element.level})"
    elif isinstance(element, ContentTextElement):
        return "Content"
    elif isinstance(element, DefinitionElement):
        return f"Definition: {element.term}" if element.term else "Definition"
    elif isinstance(element, PartyElement):
        return f"Party: {element.party_name}" if element.party_name else "Party"
    elif isinstance(element, RecitalElement):
        return "Recital"
    elif isinstance(element, SignatureBlockElement):
        return "Signature Block"
    elif isinstance(element, ExhibitElement):
        return f"Exhibit {element.exhibit_id}" if element.exhibit_id else "Exhibit"
    elif isinstance(element, TableOfContentsElement):
        return "Table of Contents"
    elif isinstance(element, MetadataElement):
        return f"Metadata ({getattr(element, 'metadata_type', 'generic')})"
    elif isinstance(element, TextElement):
        return f"TextElement (Lvl: ?)"
    else:
        return type_name


def get_element_text_preview(element: AbstractSemanticElement, max_length: int = 100) -> str:
    """Get a preview of the element's text content."""
    if hasattr(element, 'html_tag') and element.html_tag and hasattr(element.html_tag, 'text'):
        text = element.html_tag.text.strip()
    elif hasattr(element, 'text'):
        text = element.text.strip()
    else:
        text = ""

    if not text:
        return ""

    # Clean up whitespace
    text = ' '.join(text.split())

    if len(text) <= max_length:
        return text
    else:
        return text[:max_length-3] + "..."


def build_hierarchy_map(elements: List[AbstractSemanticElement]) -> Dict[str, List[str]]:
    """Build a map of parent_id -> list of child_ids."""
    hierarchy = {}

    for element in elements:
        if isinstance(element, HierarchicalElement):
            parent_id = getattr(element, 'parent_id', None)
            element_id = getattr(element, 'id', f'elem_{id(element)}')

            if parent_id:
                if parent_id not in hierarchy:
                    hierarchy[parent_id] = []
                hierarchy[parent_id].append(element_id)

    return hierarchy


def build_element_map(elements: List[AbstractSemanticElement]) -> Dict[str, AbstractSemanticElement]:
    """Build a map of element_id -> element."""
    element_map = {}

    for element in elements:
        if isinstance(element, HierarchicalElement):
            element_id = getattr(element, 'id', f'elem_{id(element)}')
            element_map[element_id] = element
        else:
            # For non-hierarchical elements, use memory id
            element_id = f'elem_{id(element)}'
            element_map[element_id] = element

    return element_map


def generate_visual_tree(elements: List[AbstractSemanticElement]) -> str:
    """Generate a visual tree representation of the semantic elements."""
    lines = []

    # Get hierarchical elements
    hierarchical_elements = [e for e in elements if isinstance(e, HierarchicalElement)]
    non_hierarchical_elements = [e for e in elements if not isinstance(e, HierarchicalElement)]

    # Build hierarchy and element maps
    hierarchy_map = build_hierarchy_map(hierarchical_elements)
    element_map = build_element_map(elements)

    # Find root elements (no parent or parent not found)
    root_elements = []
    for element in hierarchical_elements:
        parent_id = getattr(element, 'parent_id', None)
        if not parent_id or parent_id not in element_map:
            root_elements.append(element)

    # Add title elements first if they exist
    title_elements = [e for e in root_elements if isinstance(e, AgreementTitleElement)]
    other_roots = [e for e in root_elements if not isinstance(e, AgreementTitleElement)]

    # Process title elements
    for title in title_elements:
        title_text = get_element_text_preview(title, 80)
        lines.append(f"AGREEMENT: {title_text}")

        # Add children of title
        title_id = getattr(title, 'id', f'elem_{id(title)}')
        if title_id in hierarchy_map:
            _add_children_to_tree(lines, hierarchy_map, element_map, title_id, "")

    # Process other root elements
    for root in other_roots:
        lines.append("")
        lines.append("--- (Additional Root Structure) ---")
        _add_element_to_tree(lines, root, "")

        # Add children
        root_id = getattr(root, 'id', f'elem_{id(root)}')
        if root_id in hierarchy_map:
            _add_children_to_tree(lines, hierarchy_map, element_map, root_id, "")

    # Add non-hierarchical elements as additional roots
    if non_hierarchical_elements:
        for element in non_hierarchical_elements[:10]:  # Limit to first 10 to avoid clutter
            if not isinstance(element, MetadataElement):  # Skip metadata
                lines.append("")
                lines.append("--- (Additional Root Structure) ---")
                _add_element_to_tree(lines, element, "")

    return "\n".join(lines)


def _add_element_to_tree(lines: List[str], element: AbstractSemanticElement, prefix: str):
    """Add a single element to the tree with proper formatting."""
    element_type = format_element_type(element)
    text_preview = get_element_text_preview(element, 80)

    if text_preview:
        lines.append(f"{prefix}{element_type}: {text_preview}")
    else:
        lines.append(f"{prefix}{element_type}")


def _add_children_to_tree(lines: List[str], hierarchy_map: Dict[str, List[str]],
                         element_map: Dict[str, AbstractSemanticElement],
                         parent_id: str, prefix: str):
    """Recursively add children to the tree."""
    if parent_id not in hierarchy_map:
        return

    children_ids = hierarchy_map[parent_id]
    for i, child_id in enumerate(children_ids):
        if child_id not in element_map:
            continue

        child = element_map[child_id]
        is_last = (i == len(children_ids) - 1)

        # Determine prefix for this child
        if is_last:
            child_prefix = prefix + "`-- "
            next_prefix = prefix + "    "
        else:
            child_prefix = prefix + "|-- "
            next_prefix = prefix + "|   "

        # Add the child element
        element_type = format_element_type(child)
        text_preview = get_element_text_preview(child, 80)

        if text_preview:
            lines.append(f"{child_prefix}{element_type}: {text_preview}")
        else:
            lines.append(f"{child_prefix}{element_type}")

        # Add children of this child
        _add_children_to_tree(lines, hierarchy_map, element_map, child_id, next_prefix)


def process_single_file(html_file: Path, output_dir: Path) -> Dict[str, Any]:
    """Process a single HTML file and generate its visual tree."""
    try:
        # Read HTML content
        html_content = html_file.read_text(encoding='utf-8', errors='ignore')

        # Parse with new parser
        parser = AgreementParserv9Enhanced()
        elements = parser.parse(html_content)

        # Generate analysis
        file_num = int(html_file.stem.split('_')[1])  # Extract number from agreement_XXX.html
        analysis = analyze_agreement_v9_enhanced(parser, html_content, file_num)

        # Generate visual tree
        visual_tree = generate_visual_tree(elements)

        # Create output file
        output_file = output_dir / f"{html_file.stem}_tree.txt"

        # Write tree with header
        with open(output_file, 'w', encoding='utf-8') as f:
            f.write(f"Visual Semantic Tree for {html_file.name}\n")
            f.write(f"Generated using AgreementParserv9Enhanced\n")
            f.write(f"Status: {analysis.get('status', 'Unknown')}\n")
            f.write(f"Grade: {analysis.get('grade', 'N/A')}\n")
            f.write(f"Orphan Rate: {analysis.get('orphan_rate', 0):.1f}%\n")
            f.write(f"Total Elements: {len(elements)}\n")
            f.write(f"Hierarchical Elements: {len([e for e in elements if isinstance(e, HierarchicalElement)])}\n")
            f.write("=" * 80 + "\n\n")
            f.write(visual_tree)

        return {
            'file': html_file.name,
            'status': 'success',
            'elements_count': len(elements),
            'analysis': analysis
        }

    except Exception as e:
        print(f"Error processing {html_file.name}: {e}")
        return {
            'file': html_file.name,
            'status': 'error',
            'error': str(e)
        }


def main():
    """Main function to process all HTML files."""
    # Setup paths
    html_dir = Path("time_to_get_real/html_files")
    output_dir = Path("visual_semantic_trees_v3")

    # Create output directory
    output_dir.mkdir(exist_ok=True)

    # Get all HTML files
    html_files = sorted(html_dir.glob("agreement_*.html"))

    if not html_files:
        print(f"No HTML files found in {html_dir}")
        return

    print(f"Found {len(html_files)} HTML files to process")
    print(f"Output directory: {output_dir}")
    print("=" * 60)

    # Process files
    results = []
    for i, html_file in enumerate(html_files, 1):
        print(f"Processing {i:3d}/{len(html_files)}: {html_file.name}")
        result = process_single_file(html_file, output_dir)
        results.append(result)

        # Show progress
        if result['status'] == 'success':
            analysis = result['analysis']
            print(f"    ✅ {analysis.get('status', 'Unknown')} - Grade: {analysis.get('grade', 'N/A')} - Elements: {result['elements_count']}")
        else:
            print(f"    ❌ Error: {result['error']}")

    # Summary
    print("=" * 60)
    successful = sum(1 for r in results if r['status'] == 'success')
    failed = len(results) - successful

    print(f"Processing complete!")
    print(f"  ✅ Successful: {successful}")
    print(f"  ❌ Failed: {failed}")
    print(f"  📁 Output directory: {output_dir}")

    # Grade distribution
    if successful > 0:
        grades = [r['analysis'].get('grade', 'N/A') for r in results if r['status'] == 'success']
        grade_counts = {}
        for grade in grades:
            grade_counts[grade] = grade_counts.get(grade, 0) + 1

        print(f"\nGrade Distribution:")
        for grade in ['A', 'B', 'C', 'D', 'N/A']:
            count = grade_counts.get(grade, 0)
            if count > 0:
                print(f"  Grade {grade}: {count} files")


if __name__ == "__main__":
    main()
