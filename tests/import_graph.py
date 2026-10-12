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


def package_import_graph(pkg_dir, package):
    names = package_modules(pkg_dir)
    return {name: module_imports(pathlib.Path(pkg_dir) / f'{name}.py', package) & set(names)
            for name in names}


def registered_rules(app, module_prefix):
    """Sorted (rule, methods) for every route whose view lives under module_prefix."""
    return sorted(
        (r.rule, tuple(sorted(r.methods - {'HEAD', 'OPTIONS'})))
        for r in app.url_map.iter_rules()
        if app.view_functions[r.endpoint].__module__.startswith(module_prefix))


def assert_package_acyclic(pkg_dir, package, exclude=('__init__',)):
    """Group modules form an acyclic import graph and the package __init__ imports all of them."""
    assert_acyclic(package_import_graph(pkg_dir, package))
    assert module_imports(pathlib.Path(pkg_dir) / '__init__.py', package) == set(package_modules(pkg_dir, exclude))


def assert_acyclic(graph):
    try:
        list(graphlib.TopologicalSorter(graph).static_order())
    except graphlib.CycleError as exc:
        raise AssertionError(f'import cycle: {" -> ".join(exc.args[1])}') from exc
