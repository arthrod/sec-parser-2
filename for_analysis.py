#!/usr/bin/env python3
"""
Script to combine HTML sources and parser visualizations with token limiting.
Creates source_and_visualization_XXX.txt files with HTML content (limited to 80k tokens)
and corresponding parser tree results.
"""

import os
import re
from pathlib import Path
import tiktoken


def get_file_number(filename):
    """Extract the numeric part from a filename like 'agreement_001.html' or 'agreement_001_tree.txt'."""
    match = re.search(r'_(\d+)', filename)
    if match:
        return match.group(1)
    return None


def truncate_to_token_limit(text, max_tokens=80000, encoding_name="cl100k_base"):
    """Truncate text to fit within token limit."""
    try:
        encoding = tiktoken.get_encoding(encoding_name)
        tokens = encoding.encode(text)
        
        if len(tokens) <= max_tokens:
            return text, len(tokens)
        
        # Truncate tokens and decode back to text
        truncated_tokens = tokens[:max_tokens]
        truncated_text = encoding.decode(truncated_tokens)
        
        # Try to truncate at a sensible boundary (end of tag or line)
        # Look for the last complete tag
        last_close_tag = truncated_text.rfind('>')
        if last_close_tag > len(truncated_text) * 0.9:  # If we're near the end
            truncated_text = truncated_text[:last_close_tag + 1]
        
        return truncated_text, max_tokens
    except Exception as e:
        print(f"Warning: tiktoken error: {e}. Using character-based approximation.")
        # Fallback: approximate 1 token ≈ 4 characters
        char_limit = max_tokens * 4
        if len(text) <= char_limit:
            return text, len(text) // 4
        return text[:char_limit], max_tokens


def main():
    # Define directories
    html_dir = Path("time_to_get_real/html_files")
    tree_dir = Path("visual_semantic_trees_v8")
    output_dir = Path("sources_and_visualizations")
    
    # Create output directory
    output_dir.mkdir(exist_ok=True)
    
    # Check if source directories exist
    if not html_dir.exists():
        print(f"Error: HTML directory '{html_dir}' not found")
        return
    
    if not tree_dir.exists():
        print(f"Error: Tree directory '{tree_dir}' not found")
        return
    
    # Get all HTML files
    html_files = list(html_dir.glob("agreement_*.html"))
    
    if not html_files:
        print(f"No agreement_*.html files found in {html_dir}")
        return
    
    # Process each HTML file
    processed_count = 0
    token_stats = []
    
    for html_file in sorted(html_files):
        file_number = get_file_number(html_file.name)
        if not file_number:
            print(f"Warning: Could not extract number from {html_file.name}")
            continue
        
        # Find corresponding tree file
        tree_filename = f"agreement_{file_number}_tree.txt"
        tree_file = tree_dir / tree_filename
        
        if not tree_file.exists():
            print(f"Warning: Tree file not found: {tree_file}")
            continue
        
        # Read HTML content
        try:
            with open(html_file, 'r', encoding='utf-8') as f:
                html_content = f.read()
        except Exception as e:
            print(f"Error reading {html_file}: {e}")
            continue
        
        # Read tree content
        try:
            with open(tree_file, 'r', encoding='utf-8') as f:
                tree_content = f.read()
        except Exception as e:
            print(f"Error reading {tree_file}: {e}")
            continue
        
        # Truncate HTML to 80k tokens
        truncated_html, token_count = truncate_to_token_limit(html_content, max_tokens=80000)
        token_stats.append((file_number, token_count))
        
        # Create output content
        output_content = f"""<unparsed_html>
{truncated_html}
</unparsed_html>
<result_of_parser>
{tree_content}
</result_of_parser>"""
        
        # Write to output file
        output_filename = f"source_and_visualization_{file_number}.txt"
        output_path = output_dir / output_filename
        
        try:
            with open(output_path, 'w', encoding='utf-8') as f:
                f.write(output_content)
            processed_count += 1
            print(f"Created: {output_filename} (HTML: {token_count:,} tokens)")
        except Exception as e:
            print(f"Error writing {output_path}: {e}")
    
    # Summary
    print(f"\nProcessing complete!")
    print(f"Files processed: {processed_count}")
    print(f"Output directory: {output_dir}")
    
    if token_stats:
        avg_tokens = sum(t[1] for t in token_stats) / len(token_stats)
        print(f"\nToken statistics:")
        print(f"Average HTML tokens: {avg_tokens:,.0f}")
        print(f"Max HTML tokens: {max(t[1] for t in token_stats):,}")
        print(f"Min HTML tokens: {min(t[1] for t in token_stats):,}")


if __name__ == '__main__':
    # Install tiktoken if not available
    try:
        import tiktoken
    except ImportError:
        print("Installing tiktoken...")
        import subprocess
        subprocess.check_call(["pip", "install", "tiktoken"])
        import tiktoken
    
    main()