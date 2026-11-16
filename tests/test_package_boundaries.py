"""Keep package dependencies on public names."""
import ast
from pathlib import Path
import unittest


class PackageBoundaryTests(unittest.TestCase):
    def test_package_modules_do_not_import_private_names(self):
        source = Path(__file__).resolve().parents[1] / 'src' / 'proofloop'
        modules = sorted(source.glob('*.py'))
        self.assertTrue(modules)
        violations = []
        for path in modules:
            tree = ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    origin = node.module or ''
                    if node.level or origin == 'proofloop' or origin.startswith('proofloop.'):
                        names = [origin, *(alias.name for alias in node.names)]
                    else:
                        continue
                elif isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names if alias.name.startswith('proofloop.')]
                else:
                    continue
                for name in names:
                    if any(part.startswith('_') for part in name.split('.')):
                        violations.append(f'{path.name}:{node.lineno}: {name}')
        self.assertEqual(violations, [])
