import ast
from pathlib import Path


BACKEND = Path(__file__).resolve().parents[1]


def imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def test_production_entrypoints_do_not_import_mock_bootstrap():
    for relative in ("app/main.py", "app/api/auth.py"):
        modules = imported_modules(BACKEND / relative)
        assert "app.application.default_mock_bootstrap" not in modules


def test_public_mock_status_route_is_not_registered():
    from app.main import app

    paths = {route.path for route in app.routes}
    assert "/api/bootstrap/mock-status" not in paths


def test_mock_bootstrap_module_is_not_part_of_production_package():
    assert not (BACKEND / "app/application/default_mock_bootstrap.py").exists()
