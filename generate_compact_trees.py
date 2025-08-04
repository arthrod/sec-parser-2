#!/usr/bin/env python3
"""Generate compact tree format for all agreements - one line per element with type, level, and content."""

from pathlib import Path

from agreement_parser_v10_grok import LegalAgreementParser


def generate_compact_tree(agreement_num: int) -> str:
    """Generate compact tree format for a single agreement."""
    html_file = Path(f"time_to_get_real/html_files/agreement_{agreement_num:03d}.html")

    if not html_file.exists():
        return f"Agreement {agreement_num:03d}: File not found"

    try:
        html_content = html_file.read_text()
        parser = LegalAgreementParser()
        elements = parser.parse(html_content)

        lines = [f"Total parsed elements: {len(elements)}"]
        lines.append("Element types and content:")

        for i, elem in enumerate(elements):
            elem_type = type(elem).__name__

            # Get content from element
            if hasattr(elem, "text"):
                content = elem.text
            elif hasattr(elem, "html_tag") and elem.html_tag:
                content = elem.html_tag.text
            else:
                content = str(elem)

            # Clean and limit content to 200 chars
            content = content.strip().replace("\n", " ").replace("\r", "")[:200]

            # Get level
            level = getattr(elem, "level", "N/A")

            lines.append(f"  {i + 1}. {elem_type} (level={level}): {content}")

        return "\n".join(lines)

    except Exception as e:
        return f"Agreement {agreement_num:03d}: Error - {e!s}"


def main() -> None:
    """Generate compact trees for all 100 agreements."""
    output_dir = Path("compact_trees")
    output_dir.mkdir(exist_ok=True)

    for i in range(1, 101):

        tree_content = generate_compact_tree(i)

        # Save to file
        output_file = output_dir / f"agreement_{i:03d}_compact.txt"
        output_file.write_text(tree_content)

        # Print first few for verification
        if i <= 3:
            pass


if __name__ == "__main__":
    main()
