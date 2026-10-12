"""Import-graph helpers for package structure tests."""
import ast
import graphlib
import pathlib


def package_modules(pkg_dir, exclude=('__init__',)):
    return sorted(p.stem for p in pathlib.Path(pkg_dir).glob('*.py') if p.stem not in exclude)


def module_imports(path, package):
    """Names imported from `package` by `from <package> import ...` statements."""
    tree = ast.parse(pathlib.Path(path).read_text())
    return {alias.name
            for node in tree.body
            if isinstance(node, ast.ImportFrom) and node.module == package
            for alias in node.names}


def assert_acyclic(graph):
    try:
        list(graphlib.TopologicalSorter(graph).static_order())
    except graphlib.CycleError as exc:
        raise AssertionError(f'import cycle: {" -> ".join(exc.args[1])}') from exc
