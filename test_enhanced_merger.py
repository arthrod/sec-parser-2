#!/usr/bin/env python3
"""Test script for the enhanced TextElementMerger with nupunkt integration."""

import nupunkt

from agreement_parser_v10_grok import LegalAgreementParser


def test_sentence_boundary_detection() -> None:
    """Test that nupunkt correctly detects sentence boundaries."""
    tokenizer = nupunkt.PunktSentenceTokenizer()

    # Test case 1: Multiple sentences
    test_text1 = "This is the first sentence. This is the second sentence! And this is the third?"
    tokenizer.tokenize(test_text1)

    # Test case 2: Single sentence split across spans (should merge)
    test_text2 = "Property and equipment, net, co nsisted of the following (in millions):"
    tokenizer.tokenize(test_text2)

    # Test case 3: Legal text with abbreviations
    test_text3 = "The Company shall pay Mr. Smith $100,000. This amount is due on Jan. 1, 2024."
    tokenizer.tokenize(test_text3)


def test_enhanced_merger_integration() -> None:
    """Test the enhanced merger with problematic HTML that should benefit from sentence-aware merging."""
    # HTML with text split across spans but belonging to same sentence
    test_html = """<html><body>
    <div>
        <span>Property and equipment, net, co</span>
        <span>nsisted of the following (in millions):</span>
    </div>
    <div>
        <span>This is a complete sentence.</span>
        <span>This is another complete sentence.</span>
    </div>
    <div>
        <span>The Company shall pay Mr. Smith $100,000.</span>
        <span>This amount is due on Jan. 1, 2024.</span>
    </div>
    </body></html>"""

    parser = LegalAgreementParser()
    elements = parser.parse(test_html)

    for elem in elements:
        type(elem).__name__
        if hasattr(elem, "html_tag") and elem.html_tag:
            elem.html_tag.text[:200] if elem.html_tag.text else "No text"
        else:
            str(elem)[:200]
        getattr(elem, "level", "N/A")


def test_comparison_with_original() -> None:
    """Compare behavior with original TextElementMerger."""
    # Import original merger for comparison

    # Test HTML with clear sentence boundaries


if __name__ == "__main__":
    test_sentence_boundary_detection()
    test_enhanced_merger_integration()
    test_comparison_with_original()
