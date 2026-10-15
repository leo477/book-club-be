"""Static guard (no DB): string literals compared to Enum columns in app/ must be valid enum values.

SQLite accepts any string, Postgres does not (SCRUM-133: ``Event.status == "upcoming"``). This scans
``Model.column == "x"``, ``!=``, ``.in_([...])`` / ``.not_in([...])`` where Model.column is an Enum column.
Dynamic (non-literal) expressions and instance attributes (``event.status``) are not covered.
"""

import ast
from pathlib import Path

import app.models  # noqa: F401  (register all models)
from app.database import Base

APP_DIR = Path(__file__).resolve().parent.parent / "app"


def _enum_columns() -> dict[tuple[str, str], tuple[str, ...]]:
    result = {}
    for mapper in Base.registry.mappers:
        for attr in mapper.column_attrs:
            col = attr.columns[0]
            enums = getattr(col.type, "enums", None)
            if enums:
                result[(mapper.class_.__name__, attr.key)] = tuple(enums)
    return result


def _model_attr(node: ast.expr, enums) -> tuple[tuple[str, str], tuple[str, ...]] | None:
    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
        key = (node.value.id, node.attr)
        if key in enums:
            return key, enums[key]
    return None


def _literals(node: ast.expr) -> list[str]:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [node.value]
    if isinstance(node, ast.List | ast.Tuple | ast.Set):
        return [e.value for e in node.elts if isinstance(e, ast.Constant) and isinstance(e.value, str)]
    return []


def find_bad_enum_literals(source: str, enums=None) -> list[str]:
    enums = _enum_columns() if enums is None else enums
    problems = []
    for node in ast.walk(ast.parse(source)):
        pairs: list[tuple[ast.expr, ast.expr]] = []
        if isinstance(node, ast.Compare) and len(node.ops) == 1:
            pairs = [(node.left, node.comparators[0]), (node.comparators[0], node.left)]
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in {"in_", "not_in", "notin_"}
            and len(node.args) == 1
        ):
            pairs = [(node.func.value, node.args[0])]
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):  # Event(status="x")
            for kw in node.keywords:
                key = (node.func.id, kw.arg)
                if key in enums:
                    problems += [
                        f"line {node.lineno}: {key[0]}({key[1]}=...) given {v!r}, allowed {list(enums[key])}"
                        for v in _literals(kw.value)
                        if v not in enums[key]
                    ]
        for target, other in pairs:
            hit = _model_attr(target, enums)
            if not hit:
                continue
            (model, attr), allowed = hit
            problems += [
                f"line {node.lineno}: {model}.{attr} compared to {v!r}, allowed {list(allowed)}"
                for v in _literals(other)
                if v not in allowed
            ]
    return problems


def test_scanner_flags_the_scrum_133_bug():
    old = 'select(Event).where(Event.status == "upcoming", Event.status.in_(["active", "bogus"]))'
    problems = find_bad_enum_literals(old)
    assert len(problems) == 2
    assert any("'upcoming'" in p for p in problems)
    assert any("'bogus'" in p for p in problems)


def test_scanner_flags_constructor_kwargs():
    assert len(find_bad_enum_literals('Event(status="upcoming", title="x")')) == 1
    assert find_bad_enum_literals('Event(status="held")') == []


def test_scanner_accepts_valid_literals():
    assert find_bad_enum_literals('select(Event).where(Event.status.in_(["scheduled", "active"]))') == []


def test_app_has_no_invalid_enum_literals():
    assert _enum_columns(), "no Enum columns found; scanner mapping is broken"
    problems = []
    for path in sorted(APP_DIR.rglob("*.py")):
        problems += [f"{path.relative_to(APP_DIR.parent)}: {p}" for p in find_bad_enum_literals(path.read_text())]
    assert not problems, "\n".join(problems)
