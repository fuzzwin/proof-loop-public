#!/usr/bin/env python3
"""Check local Markdown links and #anchors in this source tree.

Reads Markdown files only; runs nothing and makes no network requests.
External (scheme://) links are not checked. Exit 0 when every local target
and heading anchor exists, 1 otherwise.

Usage: python3 -B tools/check_links.py [root]
"""
from pathlib import Path
import re
import sys
import unicodedata

LINK_RE = re.compile(r'(?<!!)\[[^\]]*\]\(([^)\s]+)(?:\s+"[^"]*")?\)')
HEADING_RE = re.compile(r'^(#{1,6})\s+(.*?)\s*#*\s*$')


def slug(text):
    """GitHub-style heading anchor."""
    text = re.sub(r'\[([^\]]*)\]\([^)]*\)', r'\1', text).replace('`', '').replace('*', '')
    out = []
    for char in text.strip().lower():
        if char == ' ':
            out.append('-')
        elif char in '-_' or unicodedata.category(char)[0] in 'LN':
            out.append(char)
    return ''.join(out)


def markdown_lines(path):
    """Yield (number, line) outside fenced code blocks."""
    fenced = False
    for number, line in enumerate(path.read_text(encoding='utf-8').splitlines(), 1):
        if line.lstrip().startswith('```'):
            fenced = not fenced
        elif not fenced:
            yield number, line


def anchors(path, cache={}):
    if path not in cache:
        seen, found = {}, set()
        for _, line in markdown_lines(path):
            match = HEADING_RE.match(line)
            if match:
                base = slug(match.group(2))
                count = seen.get(base, 0)
                found.add(base if count == 0 else f'{base}-{count}')
                seen[base] = count + 1
        cache[path] = found
    return cache[path]


def check(root):
    problems, checked = [], 0
    for document in sorted(root.rglob('*.md')):
        if '.git' in document.relative_to(root).parts:
            continue
        for number, line in markdown_lines(document):
            for target in LINK_RE.findall(line):
                if re.match(r'^[A-Za-z][A-Za-z0-9+.-]*:', target):
                    continue
                checked += 1
                path_part, _, fragment = target.partition('#')
                destination = (document.parent / path_part).resolve() if path_part else document
                where = f'{document.relative_to(root)}:{number}'
                if not destination.exists():
                    problems.append(f'{where}: missing target {target}')
                elif fragment and destination.suffix == '.md' and fragment not in anchors(destination):
                    problems.append(f'{where}: missing anchor #{fragment}')
    return checked, problems


def main(argv):
    root = Path(argv[1] if len(argv) > 1 else Path(__file__).resolve().parents[1]).resolve()
    checked, problems = check(root)
    for problem in problems:
        print(problem)
    print(f'local Markdown links checked: {checked}; problems: {len(problems)}')
    return 1 if problems else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
