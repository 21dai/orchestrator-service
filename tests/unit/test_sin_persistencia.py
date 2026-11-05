"""El orquestador no tiene persistencia propia (ADR-0003, issue #22).

Guardar es trabajo de document-store: el orquestador no declara drivers de
base de datos, no los importa y su configuración no tiene datos de conexión.
"""

import ast
import re
import tomllib
from pathlib import Path

from app.config import Settings

REPO_ROOT = Path(__file__).resolve().parents[2]

# Drivers y ORMs de las bases más comunes (nombres normalizados de PyPI).
DATABASE_PACKAGES = frozenset(
    {
        "pymongo",
        "motor",
        "beanie",
        "mongoengine",
        "odmantic",
        "sqlalchemy",
        "sqlmodel",
        "alembic",
        "psycopg",
        "psycopg2",
        "psycopg2-binary",
        "asyncpg",
        "aiosqlite",
        "pymysql",
        "aiomysql",
        "mysqlclient",
        "redis",
        "tortoise-orm",
        "peewee",
        "databases",
    }
)
DATABASE_MODULES = frozenset(
    name.replace("-", "_").removesuffix("_binary").removesuffix("_orm")
    for name in DATABASE_PACKAGES
) | {"sqlite3", "MySQLdb", "tortoise", "bson"}

# Palabras que delatan un campo de conexión a una base en Settings.
DATABASE_FIELD_WORDS = frozenset({"database", "db", "mongo", "mongodb", "sql", "redis", "dsn"})


def _package_name(requirement: str) -> str:
    """`"pymongo[srv]>=4.0"` → `"pymongo"`, normalizado como en PyPI."""
    name = re.split(r"[\s\[<>=!~;@]", requirement.strip(), maxsplit=1)[0]
    return re.sub(r"[-_.]+", "-", name).lower()


def test_pyproject_no_declara_drivers_de_base_de_datos() -> None:
    pyproject = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    requirements = list(pyproject["project"]["dependencies"])
    for group in pyproject.get("dependency-groups", {}).values():
        requirements += [item for item in group if isinstance(item, str)]

    declared = {_package_name(requirement) for requirement in requirements}

    assert declared, "No se leyeron dependencias del pyproject.toml"
    assert declared.isdisjoint(DATABASE_PACKAGES)


def test_el_codigo_de_app_no_importa_drivers_de_base_de_datos() -> None:
    imported: set[str] = set()
    for path in (REPO_ROOT / "app").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported |= {alias.name.split(".")[0] for alias in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                imported.add(node.module.split(".")[0])

    assert "fastapi" in imported, "No se recorrió el código de app/"
    assert imported.isdisjoint(DATABASE_MODULES)


def test_settings_no_tiene_campos_de_conexion_a_una_base() -> None:
    campos_de_base = [
        field
        for field in Settings.model_fields
        if DATABASE_FIELD_WORDS & set(field.lower().split("_"))
        or "mongo" in field.lower()
        or "sql" in field.lower()
    ]

    assert campos_de_base == []
