from pathlib import Path


DOMAIN_ROOT = Path(__file__).resolve().parents[2] / "app" / "domain"

# Baseline exceptions to keep runtime stable while we migrate dependencies by
# slices. New files must not be added here.
ALLOWED_DOMAIN_INFRA_IMPORTS: set[str] = set()

ALLOWED_DOMAIN_AI_IMPORTS: set[str] = set()


def _collect_import_offenders(root: Path, needles: tuple[str, ...]) -> list[str]:
    offenders: list[str] = []
    for py_file in root.rglob("*.py"):
        rel = py_file.relative_to(root).as_posix()
        content = py_file.read_text(encoding="utf-8")
        if any(needle in content for needle in needles):
            offenders.append(rel)
    return sorted(set(offenders))


def test_domain_layer_does_not_add_new_infrastructure_imports() -> None:
    offenders = _collect_import_offenders(
        DOMAIN_ROOT,
        ("from app.infrastructure", "import app.infrastructure"),
    )
    unexpected = sorted(set(offenders) - ALLOWED_DOMAIN_INFRA_IMPORTS)
    assert not unexpected, (
        "New app.domain -> app.infrastructure imports detected: "
        f"{unexpected}. Move dependency behind domain ports/adapters."
    )


def test_domain_layer_does_not_add_new_ai_imports() -> None:
    offenders = _collect_import_offenders(
        DOMAIN_ROOT,
        ("from app.ai", "import app.ai"),
    )
    unexpected = sorted(set(offenders) - ALLOWED_DOMAIN_AI_IMPORTS)
    assert not unexpected, (
        "New app.domain -> app.ai imports detected: "
        f"{unexpected}. Move dependency behind domain ports/application services."
    )
