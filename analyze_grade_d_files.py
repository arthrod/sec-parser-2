#!/usr/bin/env python3
"""
Analyze Grade D files from visual_semantic_trees_v4 and create comprehensive analysis.

This script:
1. Goes through visual_semantic_trees_v4 files
2. Identifies files with Grade D or worse
3. Concatenates content from:
   - The v4 tree file
   - The corresponding file from visual_semantic_trees__/
   - The original HTML file (truncated to 5k tokens using tiktoken)
4. Creates a comprehensive analysis file
"""

import re
from pathlib import Path
from typing import List, Dict, Any, Optional
import tiktoken

def count_tokens(text: str, model: str = "gpt-4") -> int:
    """Count tokens in text using tiktoken."""
    try:
        encoding = tiktoken.encoding_for_model(model)
        return len(encoding.encode(text))
    except Exception as e:
        raise RuntimeError(f"tiktoken failed to count tokens: {e}. Please install tiktoken: pip install tiktoken")

def truncate_to_tokens(text: str, max_tokens: int = 511, model: str = "gpt-4") -> str:
    """Truncate text to approximately max_tokens."""
    try:
        encoding = tiktoken.encoding_for_model(model)
        tokens = encoding.encode(text)
        if len(tokens) <= max_tokens:
            return text

        # Truncate tokens and decode back
        truncated_tokens = tokens[:max_tokens]
        truncated_text = encoding.decode(truncated_tokens)
        return truncated_text + "\n\n[TRUNCATED - Content exceeded 511 tokens]"

    except Exception as e:
        raise RuntimeError(f"tiktoken failed to truncate text: {e}. Please install tiktoken: pip install tiktoken")

def extract_grade_from_tree(tree_file: Path) -> Optional[str]:
    """Extract grade from a tree file."""
    try:
        content = tree_file.read_text(encoding='utf-8', errors='ignore')

        # Look for "Grade: X" pattern in the header
        grade_match = re.search(r'Grade:\s*([A-DF]|N/A)', content)
        if grade_match:
            return grade_match.group(1)

        return None
    except Exception as e:
        print(f"Error reading {tree_file}: {e}")
        return None

def get_corresponding_files(agreement_num: str) -> Dict[str, Optional[Path]]:
    """Get corresponding files for an agreement number."""
    files = {}

    # V4 tree file
    v4_file = Path(f"visual_semantic_trees_v4/agreement_{agreement_num}_tree.txt")
    files['v4_tree'] = v4_file if v4_file.exists() else None

    # Original tree file
    orig_tree_file = Path(f"visual_semantic_trees_v3/agreement_{agreement_num}_tree.txt")
    files['orig_tree'] = orig_tree_file if orig_tree_file.exists() else None

    # HTML file
    html_file = Path(f"time_to_get_real/html_files/agreement_{agreement_num}.html")
    files['html'] = html_file if html_file.exists() else None

    return files

def read_file_safe(file_path: Path, max_tokens: int = None) -> str:
    """Safely read a file with optional token truncation."""
    try:
        content = file_path.read_text(encoding='utf-8', errors='ignore')
        if max_tokens:
            content = truncate_to_tokens(content, max_tokens)
        return content
    except Exception as e:
        return f"[ERROR READING FILE: {e}]"

def analyze_grade_d_files() -> List[Dict[str, Any]]:
    """Analyze all files and identify Grade D or worse."""
    v4_dir = Path("visual_semantic_trees_v4")

    if not v4_dir.exists():
        print(f"Directory {v4_dir} does not exist!")
        return []

    grade_d_files = []

    # Process all tree files in v4 directory
    tree_files = sorted(v4_dir.glob("agreement_*_tree.txt"))

    for tree_file in tree_files:
        # Extract agreement number
        match = re.search(r'agreement_(\d+)_tree\.txt', tree_file.name)
        if not match:
            continue

        agreement_num = match.group(1)
        grade = extract_grade_from_tree(tree_file)

        # Check if grade is D or worse (including N/A, errors, etc.)
        if grade in ['D', 'N/A'] or grade is None:
            print(f"Found Grade D file: agreement_{agreement_num} (Grade: {grade})")

            # Get corresponding files
            files = get_corresponding_files(agreement_num)

            grade_d_files.append({
                'agreement_num': agreement_num,
                'grade': grade,
                'files': files
            })

    return grade_d_files

def generate_analysis_report(grade_d_files: List[Dict[str, Any]]) -> str:
    """Generate the comprehensive analysis report."""
    report_lines = []

    # Header
    report_lines.append("COMPREHENSIVE ANALYSIS OF GRADE D FILES")
    report_lines.append("=" * 80)
    report_lines.append("Generated using AgreementParserv9Enhanced")
    report_lines.append(f"Total Grade D files found: {len(grade_d_files)}")
    report_lines.append("")

    # Add parser content first
    report_lines.append("<result_of_script_entitled starting_anew_parser_v4.py>")
    try:
        parser_content = Path("starting_anew_parser_v4.py").read_text(encoding='utf-8')
        # Truncate parser content to reasonable size
        parser_content = truncate_to_tokens(parser_content, 3000)
        report_lines.append(parser_content)
    except Exception as e:
        report_lines.append(f"[ERROR READING starting_anew_parser_v4.py: {e}]")
    report_lines.append("</result_of_script_entitled starting_anew_parser_v4.py>")
    report_lines.append("")

    # Add comparison parser if it exists
    comparison_parser = Path("starting_anew_parser_v3.py")
    if comparison_parser.exists():
        report_lines.append("<result_of_script_entitled starting_anew_parser_v3.py>")
        try:
            comparison_content = comparison_parser.read_text(encoding='utf-8')
            comparison_content = truncate_to_tokens(comparison_content, 3000)
            report_lines.append(comparison_content)
        except Exception as e:
            report_lines.append(f"[ERROR READING starting_anew_parser_v3.py: {e}]")
        report_lines.append("</result_of_script_entitled starting_anew_parser_v3.py>")
        report_lines.append("")

    # Process each Grade D file
    for i, file_info in enumerate(grade_d_files, 1):
        agreement_num = file_info['agreement_num']
        grade = file_info['grade']
        files = file_info['files']

        report_lines.append(f"GRADE D FILE #{i}: agreement_{agreement_num} (Grade: {grade})")
        report_lines.append("-" * 60)

        # Add V4 tree
        if files['v4_tree']:
            report_lines.append(f"<v4_semantic_tree_agreement_{agreement_num}>")
            v4_content = read_file_safe(files['v4_tree'])
            report_lines.append(v4_content)
            report_lines.append(f"</v4_semantic_tree_agreement_{agreement_num}>")
            report_lines.append("")

        # Add original tree if available
        #if files['orig_tree']:
         #   report_lines.append(f"<original_semantic_tree_agreement_{agreement_num}>")
          #  orig_content = read_file_safe(files['orig_tree'])
           # report_lines.append(orig_content)
           # report_lines.append(f"</original_semantic_tree_agreement_{agreement_num}>")
           # report_lines.append("")
           #else:
            #report_lines.append(f"<original_semantic_tree_agreement_{agreement_num}>")
            #report_lines.append("[ORIGINAL TREE FILE NOT FOUND]")
            #report_lines.append(f"</original_semantic_tree_agreement_{agreement_num}>")
            #report_lines.append("")

        # Add HTML content (truncated to 511 tokens)
        if files['html']:
            report_lines.append(f"<content_of_origin_html_agreement_{agreement_num}>")
            html_content = read_file_safe(files['html'], max_tokens=511)
            report_lines.append(html_content)
            report_lines.append(f"</content_of_origin_html_agreement_{agreement_num}>")
        else:
            report_lines.append(f"<content_of_origin_html_agreement_{agreement_num}>")
            report_lines.append("[HTML FILE NOT FOUND]")
            report_lines.append(f"</content_of_origin_html_agreement_{agreement_num}>")

        report_lines.append("")
        report_lines.append("=" * 80)
        report_lines.append("")

    return "\n".join(report_lines)

def main():
    """Main function."""
    # Check if tiktoken is available
    try:
        import tiktoken
        tiktoken.encoding_for_model("gpt-4")
    except ImportError:
        raise RuntimeError("tiktoken is required but not installed. Please install it: pip install tiktoken")
    except Exception as e:
        raise RuntimeError(f"tiktoken is not working properly: {e}")

    print("Analyzing Grade D files from starting_anew_parser_v4...")
    print("=" * 60)

    # Find Grade D files
    grade_d_files = analyze_grade_d_files()

    if not grade_d_files:
        print("No Grade D files found!")
        return

    print(f"\nFound {len(grade_d_files)} Grade D files:")
    for file_info in grade_d_files:
        agreement_num = file_info['agreement_num']
        grade = file_info['grade']
        print(f"  - agreement_{agreement_num} (Grade: {grade})")

    print("\nGenerating comprehensive analysis report...")

    # Generate report
    report = generate_analysis_report(grade_d_files)

    # Write report
    output_file = Path("grade_d_analysis_report_v4.txt")
    try:
        output_file.write_text(report, encoding='utf-8')
        print(f"✅ Report generated: {output_file}")

        # Show file size and token count
        file_size = output_file.stat().st_size
        token_count = count_tokens(report)
        print(f"   File size: {file_size:,} bytes")
        print(f"   Estimated tokens: {token_count:,}")

    except Exception as e:
        print(f"❌ Error writing report: {e}")

    print("\nAnalysis complete!")

if __name__ == "__main__":
    main()
