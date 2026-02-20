import asyncio
import importlib
import sys
import types

import pytest


def _load_manager_module():
    if "structlog" not in sys.modules:
        fake_structlog = types.ModuleType("structlog")
        fake_structlog.get_logger = lambda *args, **kwargs: types.SimpleNamespace(
            info=lambda *a, **k: None,
            warning=lambda *a, **k: None,
            error=lambda *a, **k: None,
            debug=lambda *a, **k: None,
        )
        sys.modules["structlog"] = fake_structlog
    return importlib.import_module("app.infrastructure.concurrency.tenant_concurrency_manager")


def test_tenant_slot_releases_doc_lock_after_exception() -> None:
    mod = _load_manager_module()
    manager = mod.TenantConcurrencyManager(per_tenant_limit=1)
    global_sem = asyncio.Semaphore(1)

    async def _run() -> None:
        with pytest.raises(RuntimeError, match="boom"):
            async with manager.tenant_slot(
                record={"institution_id": "tenant-a"},
                doc_id="doc-1",
                global_semaphore=global_sem,
            ):
                raise RuntimeError("boom")

        acquired_again = await manager.try_acquire_doc_lock("doc-1")
        assert acquired_again is True
        await manager.release_doc_lock("doc-1")

    asyncio.run(_run())


def test_tenant_slot_raises_when_doc_already_processing() -> None:
    mod = _load_manager_module()
    manager = mod.TenantConcurrencyManager(per_tenant_limit=1)
    global_sem = asyncio.Semaphore(1)

    async def _run() -> None:
        acquired = await manager.try_acquire_doc_lock("doc-2")
        assert acquired is True
        try:
            with pytest.raises(mod.AlreadyProcessingError):
                async with manager.tenant_slot(
                    record={"institution_id": "tenant-b"},
                    doc_id="doc-2",
                    global_semaphore=global_sem,
                ):
                    pass
        finally:
            await manager.release_doc_lock("doc-2")

    asyncio.run(_run())
