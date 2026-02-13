from pathlib import Path


APP_ROOT = Path(__file__).resolve().parents[2] / "app" / "application"

# Sprint 1 baseline: these files still use direct Supabase client access and
# are scheduled for migration in follow-up slices.
ALLOWED_DIRECT_SUPABASE_FILES = {
    "services/retrieval_router.py",
    "services/visual_anchor_service.py",
}


def test_application_layer_does_not_add_new_direct_supabase_calls() -> None:
    offenders: list[str] = []

    for py_file in APP_ROOT.rglob("*.py"):
        rel = py_file.relative_to(APP_ROOT).as_posix()
        content = py_file.read_text(encoding="utf-8")
        if "get_async_supabase_client" not in content:
            continue
        offenders.append(rel)

    unexpected = sorted(set(offenders) - ALLOWED_DIRECT_SUPABASE_FILES)
    assert not unexpected, (
        "New direct Supabase calls were introduced in application layer: "
        f"{unexpected}. Route data access through infrastructure repositories/services."
    )
