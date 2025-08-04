#!/usr/bin/env python3
"""
Script to extract imported content from Python files.
Usage: python extract_imports.py [--full]
"""

import ast
import argparse
import os
import re
from pathlib import Path
from typing import List, Tuple, Set


def parse_imports(content: str) -> List[Tuple[str, List[str]]]:
    """Parse import statements from content and return (module_path, [imported_items])."""
    imports = []
    
    # Handle multi-line imports
    content = re.sub(r'\\\s*\n', ' ', content)
    
    # Parse with AST
    try:
        tree = ast.parse(content)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                module = node.module
                items = [alias.name for alias in node.names]
                if module:
                    imports.append((module, items))
    except:
        # Fallback to regex if AST parsing fails
        # Pattern for: from X import Y
        pattern = r'from\s+([^\s]+)\s+import\s+\(([^)]+)\)|from\s+([^\s]+)\s+import\s+([^\n]+)'
        
        for match in re.finditer(pattern, content):
            if match.group(1):  # Multi-line import
                module = match.group(1)
                items_str = match.group(2)
                items = [item.strip() for item in items_str.split(',')]
            else:  # Single line import
                module = match.group(3)
                items_str = match.group(4)
                items = [item.strip() for item in items_str.split(',')]
            
            imports.append((module, items))
    
    return imports


def module_to_filepath(module_path: str, search_paths: List[str] = None) -> str:
    """Convert module path to file path."""
    if search_paths is None:
        search_paths = ['.', '..', '../..']  # Common search paths
    
    # Convert module path to file path
    file_path = module_path.replace('.', os.sep) + '.py'
    
    # Search for the file
    for search_path in search_paths:
        full_path = os.path.join(search_path, file_path)
        if os.path.exists(full_path):
            return full_path
    
    # If not found, return the constructed path anyway
    return file_path


def extract_definition(file_content: str, name: str) -> str:
    """Extract a specific class, function, or variable definition from file content."""
    try:
        tree = ast.parse(file_content)
        
        for node in tree.body:
            # Check for class definitions
            if isinstance(node, ast.ClassDef) and node.name == name:
                return ast.get_source_segment(file_content, node)
            
            # Check for function definitions
            elif isinstance(node, ast.FunctionDef) and node.name == name:
                return ast.get_source_segment(file_content, node)
            
            # Check for assignments (variables/constants)
            elif isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.name == name:
                        return ast.get_source_segment(file_content, node)
            
            # Check for imports that might re-export
            elif isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    if alias.name == name or (alias.asname and alias.asname == name):
                        return f"# Re-exported from {node.module}\n{ast.get_source_segment(file_content, node)}"
    
    except Exception as e:
        # Fallback to regex-based extraction
        # Try to find class definition
        class_match = re.search(rf'^class\s+{name}\b.*?(?=\n(?:class|def|\Z))', 
                               file_content, re.MULTILINE | re.DOTALL)
        if class_match:
            return class_match.group(0)
        
        # Try to find function definition
        func_match = re.search(rf'^def\s+{name}\b.*?(?=\n(?:class|def|\Z))', 
                              file_content, re.MULTILINE | re.DOTALL)
        if func_match:
            return func_match.group(0)
        
        # Try to find variable assignment
        var_match = re.search(rf'^{name}\s*=.*$', file_content, re.MULTILINE)
        if var_match:
            return var_match.group(0)
    
    return f"# Could not extract definition for: {name}"


def main():
    parser = argparse.ArgumentParser(description='Extract imported content from Python files')
    parser.add_argument('--full', action='store_true', help='Extract full file content instead of just imported items')
    args = parser.parse_args()
    
    # Read import statements
    try:
        with open('to_import.txt', 'r') as f:
            import_content = f.read()
    except FileNotFoundError:
        print("Error: to_import.txt not found")
        return
    
    # Parse imports
    imports = parse_imports(import_content)
    
    # Process imports and collect output
    output_parts = []
    processed_files = set()
    
    for module_path, items in imports:
        file_path = module_to_filepath(module_path)
        
        # Create a tag name from the file name
        file_name = os.path.basename(file_path).replace('.py', '')
        
        try:
            with open(file_path, 'r') as f:
                file_content = f.read()
        except FileNotFoundError:
            output_parts.append(f"<{file_name}>\n# Error: File not found: {file_path}\n</{file_name}>\n")
            continue
        
        if args.full:
            # Extract full file content
            if file_path not in processed_files:
                output_parts.append(f"<{file_name}>\n{file_content}\n</{file_name}>\n")
                processed_files.add(file_path)
        else:
            # Extract only imported items
            for item in items:
                definition = extract_definition(file_content, item)
                output_parts.append(f"<{item}>\n{definition}\n</{item}>\n")
    
    # Write output
    with open('output.txt', 'w') as f:
        f.write('\n'.join(output_parts))
    
    print(f"Extraction complete. Results saved to output.txt")
    print(f"Mode: {'Full file content' if args.full else 'Imported items only'}")


if __name__ == '__main__':
    main()