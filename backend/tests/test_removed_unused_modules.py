import ast
from pathlib import Path


BACKEND = Path(__file__).resolve().parents[1]


def test_unreferenced_account_precheck_module_is_removed():
    assert not (BACKEND / "app/agents/account_precheck.py").exists()


def test_chat_api_has_no_legacy_get_graph_test_hook():
    tree = ast.parse((BACKEND / "app/api/chat.py").read_text(encoding="utf-8"))
    functions = {node.name for node in tree.body if isinstance(node, ast.FunctionDef)}
    assert "get_graph" not in functions


def test_legacy_connection_registry_has_no_production_references():
    production = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (BACKEND / "app").rglob("*.py")
        if path.name != "connection_registry.py"
    )
    assert "connection_registry" not in production
