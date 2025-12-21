#!/usr/bin/env python3
"""
Regression guard for AgreementParserv9Enhanced

This script provides regression testing capability by:
1. Building a baseline metrics set (JSON) from all HTML files under a directory
2. Comparing current runs vs. baseline and failing with non-zero exit code if:
   - orphan_rate increases by more than delta (default 0.5 percentage points), or
   - total sections+articles decreases by more than 10% for any individual document

Usage:
    # First, generate a baseline after improvements are in good state:
    python regression_guard.py --html-dir time_to_get_real/html_files --baseline baseline.json --save-baseline
    
    # Later, on a new commit, run:
    python regression_guard.py --html-dir time_to_get_real/html_files --baseline baseline.json
    # It will exit 1 if regressions are detected.
"""

import argparse
import json
from pathlib import Path
from collections import defaultdict

from sec_parser.processing_engine.core import AbstractSemanticElementParser

# Import the parser - adjust import path as needed
from starting_anew_parser_v3 import AgreementParserv9Enhanced, analyze_agreement_v9_enhanced


def run_once(html_dir: Path) -> list[dict]:
    """
    Run the parser on all HTML files in the directory and collect metrics.
    
    Args:
        html_dir: Directory containing HTML files to process
        
    Returns:
        List of dictionaries containing compact metrics for each file
    """
    results = []
    files = sorted(html_dir.glob("*.html"))
    
    for i, f in enumerate(files, 1):
        # Create fresh parser for each file for clean state
        parser = AgreementParserv9Enhanced()
        
        try:
            html = f.read_text(errors="ignore")
            res = analyze_agreement_v9_enhanced(parser, html, i)
            
            # Keep only small metrics we compare
            compact = {
                "file": f.name,
                "orphan_rate": res.get("orphan_rate", 0.0),
                "orphan_count": res.get("orphan_count", 0),
                "hierarchy_depth": res.get("hierarchy_depth", 0),
                "type_counts": res.get("type_counts", {}),
            }
            results.append(compact)
            
        except Exception as e:
            print(f"Error processing {f.name}: {e}")
            # Add error entry to maintain file order consistency
            compact = {
                "file": f.name,
                "orphan_rate": 100.0,  # High orphan rate indicates failure
                "orphan_count": 0,
                "hierarchy_depth": 0,
                "type_counts": {},
                "error": str(e),
            }
            results.append(compact)
    
    return results


def save_baseline(results: list[dict], outfile: Path) -> None:
    """
    Save baseline metrics to JSON file.
    
    Args:
        results: List of metric dictionaries to save
        outfile: Path to output JSON file
    """
    outfile.write_text(json.dumps(results, indent=2))


def load_baseline(infile: Path) -> list[dict]:
    """
    Load baseline metrics from JSON file.
    
    Args:
        infile: Path to baseline JSON file
        
    Returns:
        List of baseline metric dictionaries
    """
    return json.loads(infile.read_text())


def compare(baseline: list[dict], current: list[dict], max_orphan_delta: float = 0.5, min_struct_ratio: float = 0.9) -> list[tuple[str, str]]:
    """
    Compare current results against baseline and identify regressions.
    
    Args:
        baseline: Baseline metrics
        current: Current run metrics
        max_orphan_delta: Maximum allowed increase in orphan rate (percentage points)
        min_struct_ratio: Minimum allowed ratio of structural elements (sections + articles)
        
    Returns:
        List of (filename, failure_message) tuples for any regressions found
    """
    failures = []
    
    # Create lookup dictionaries by filename
    base_by_file = {r["file"]: r for r in baseline}
    cur_by_file = {r["file"]: r for r in current}
    
    # Check files that exist in both baseline and current
    files = sorted(set(base_by_file.keys()) & set(cur_by_file.keys()))
    
    for fn in files:
        b = base_by_file[fn]
        c = cur_by_file[fn]
        
        # Skip files that had errors in either run
        if "error" in b or "error" in c:
            continue
            
        # Check orphan rate regression
        orphan_increase = c["orphan_rate"] - b["orphan_rate"]
        if orphan_increase > max_orphan_delta:
            failures.append((
                fn, 
                f"Orphan rate regression: {b['orphan_rate']:.2f}% -> {c['orphan_rate']:.2f}% (+{orphan_increase:.2f}%)"
            ))
        
        # Check structural coverage: sections + articles
        b_sections = b["type_counts"].get("SectionElement", 0) + b["type_counts"].get("ArticleElement", 0)
        c_sections = c["type_counts"].get("SectionElement", 0) + c["type_counts"].get("ArticleElement", 0)
        
        if b_sections > 0:  # Only check if baseline had structural elements
            if c_sections < b_sections * min_struct_ratio:
                coverage_ratio = c_sections / b_sections if b_sections > 0 else 0
                failures.append((
                    fn, 
                    f"Structural coverage decreased: {b_sections} -> {c_sections} ({coverage_ratio:.1%} of baseline)"
                ))
    
    # Check for files that disappeared
    missing_files = set(base_by_file.keys()) - set(cur_by_file.keys())
    for fn in missing_files:
        failures.append((fn, "File missing from current run"))
    
    return failures


def main():
    """Main entry point for the regression guard script."""
    ap = argparse.ArgumentParser(
        description="Regression guard for AgreementParserv9Enhanced",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Save baseline after implementing improvements:
  python regression_guard.py --html-dir time_to_get_real/html_files --baseline baseline.json --save-baseline
  
  # Check for regressions:
  python regression_guard.py --html-dir time_to_get_real/html_files --baseline baseline.json
        """
    )
    
    ap.add_argument(
        "--html-dir", 
        default="time_to_get_real/html_files", 
        help="Directory containing *.html files to process"
    )
    ap.add_argument(
        "--baseline", 
        required=True, 
        help="Path to baseline.json (for save or compare)"
    )
    ap.add_argument(
        "--save-baseline", 
        action="store_true", 
        help="Save new baseline from current run"
    )
    ap.add_argument(
        "--max-orphan-delta", 
        type=float, 
        default=0.5, 
        help="Allowed orphan_rate increase (percentage points, default: 0.5)"
    )
    ap.add_argument(
        "--min-struct-ratio", 
        type=float, 
        default=0.9, 
        help="Allowed ratio of (sections+articles) vs baseline (default: 0.9)"
    )
    
    args = ap.parse_args()
    
    html_dir = Path(args.html_dir)
    baseline_path = Path(args.baseline)
    
    # Validate HTML directory exists
    if not html_dir.exists():
        print(f"Error: HTML directory '{html_dir}' does not exist")
        raise SystemExit(1)
    
    # Run parser on all HTML files
    print(f"Processing HTML files in {html_dir}...")
    current = run_once(html_dir)
    print(f"Processed {len(current)} files")
    
    if args.save_baseline:
        # Save baseline mode
        save_baseline(current, baseline_path)
        print(f"Saved baseline to {baseline_path}")
        
        # Print summary of baseline
        total_files = len(current)
        error_files = sum(1 for r in current if "error" in r)
        avg_orphan_rate = sum(r["orphan_rate"] for r in current if "error" not in r) / max(1, total_files - error_files)
        total_sections = sum(r["type_counts"].get("SectionElement", 0) for r in current)
        total_articles = sum(r["type_counts"].get("ArticleElement", 0) for r in current)
        
        print(f"Baseline summary:")
        print(f"  - Files processed: {total_files}")
        print(f"  - Files with errors: {error_files}")
        print(f"  - Average orphan rate: {avg_orphan_rate:.2f}%")
        print(f"  - Total sections: {total_sections}")
        print(f"  - Total articles: {total_articles}")
        return
    
    # Compare mode
    if not baseline_path.exists():
        print(f"Error: Baseline file '{baseline_path}' does not exist")
        print("Run with --save-baseline first to create a baseline")
        raise SystemExit(1)
    
    print(f"Loading baseline from {baseline_path}...")
    baseline = load_baseline(baseline_path)
    
    print(f"Comparing current run against baseline...")
    failures = compare(baseline, current, args.max_orphan_delta, args.min_struct_ratio)
    
    if failures:
        print("\n🚨 REGRESSION FAILURES DETECTED:")
        print("=" * 50)
        for fn, msg in failures:
            print(f"  ❌ {fn}: {msg}")
        print("=" * 50)
        print(f"Total failures: {len(failures)}")
        raise SystemExit(1)
    
    print("✅ No regressions detected.")
    
    # Print comparison summary
    total_files = len(current)
    baseline_files = len(baseline)
    print(f"\nComparison summary:")
    print(f"  - Current files: {total_files}")
    print(f"  - Baseline files: {baseline_files}")
    print(f"  - Files compared: {len(set(r['file'] for r in baseline) & set(r['file'] for r in current))}")
    print(f"  - Orphan rate threshold: ≤{args.max_orphan_delta}% increase")
    print(f"  - Structure ratio threshold: ≥{args.min_struct_ratio:.0%} of baseline")


if __name__ == "__main__":
    main()