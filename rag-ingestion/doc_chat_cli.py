"""
doc_chat_cli.py — CLI de Auditoría Documental con Recuperación Híbrida
======================================================================
Estrategia Dual:
  1. Vector Search (Chunks específicos del documento, filtrados por source_id)
  2. RAPTOR Summaries (Resúmenes jerárquicos de alto nivel)

Ambas consultas se disparan en paralelo con asyncio.gather para máxima velocidad.
"""

import asyncio
import argparse
import os
import sys
from pathlib import Path
from typing import List, Dict, Any, Optional
from dotenv import load_dotenv
from groq import Groq

# Importar herramientas locales
sys.path.append(os.getcwd())
from app.infrastructure.repositories.supabase_retrieval_repository import SupabaseRetrievalRepository
from app.core.tools.retrieval import RetrievalTools
from app.infrastructure.supabase.client import get_async_supabase_client
from app.mas_simple.application import HandleQuestionCommand, HandleQuestionUseCase
from app.mas_simple.infrastructure.adapters import (
    GroqAnswerGeneratorAdapter,
    LiteralEvidenceValidator,
    RetrievalToolsAdapter,
)

# Configuración
here = Path(__file__).resolve()
rag_dir = here.parent
repo_root = here.parents[1]
for env_file in (rag_dir / ".env", rag_dir / ".env.local", repo_root / ".env", repo_root / ".env.local"):
    if env_file.exists():
        load_dotenv(dotenv_path=env_file, override=False)

GROQ_API_KEY = (os.getenv("GROQ_API_KEY") or "").strip()
if not GROQ_API_KEY:
    print("❌ Falta GROQ_API_KEY para iniciar MAS Simple.")
    print("💡 Define GROQ_API_KEY en variables de entorno o en .env.local del repo.")
    sys.exit(1)

MODEL_NAME = "llama-3.3-70b-versatile"

client = Groq(api_key=GROQ_API_KEY)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers de Supabase
# ─────────────────────────────────────────────────────────────────────────────

async def list_documents(limit: int = 50) -> List[Dict[str, Any]]:
    """Consulta la tabla source_documents para listar los archivos disponibles."""
    supabase = await get_async_supabase_client()
    try:
        res = await supabase.table("source_documents") \
            .select("id, filename, is_global, institution_id, collection_id, metadata, created_at") \
            .order("created_at", desc=True) \
            .limit(limit) \
            .execute()
        return res.data or []
    except Exception as e:
        print(f"❌ Error al listar documentos: {e}")
        return []


def _matches_collection(doc: Dict[str, Any], metadata: Dict[str, Any], collection_id: Optional[str], collection_name: Optional[str]) -> bool:
    if not collection_id and not collection_name:
        return True

    doc_collection_id = doc.get("collection_id") or metadata.get("collection_id") or metadata.get("folder_id")
    doc_collection_name = metadata.get("collection_name") or metadata.get("folder_name")

    if collection_id and str(doc_collection_id) == str(collection_id):
        return True
    if collection_name and str(doc_collection_name).strip().lower() == str(collection_name).strip().lower():
        return True

    return False


async def list_documents_for_scope(
    tenant_id: str,
    collection_id: Optional[str] = None,
    collection_name: Optional[str] = None,
) -> List[Dict[str, Any]]:
    docs = await list_documents(limit=500)
    scoped: List[Dict[str, Any]] = []
    for doc in docs:
        doc_tenant = doc.get("institution_id")
        metadata = doc.get("metadata") or {}
        if not doc_tenant:
            doc_tenant = metadata.get("institution_id") or metadata.get("tenant_id")
        if str(doc_tenant or "") != str(tenant_id):
            continue
        if not _matches_collection(doc, metadata, collection_id, collection_name):
            continue
        scoped.append(doc)
    return scoped


# ─────────────────────────────────────────────────────────────────────────────
# Motor MAS Simple
# ─────────────────────────────────────────────────────────────────────────────

async def query_engine(
    tools: RetrievalTools,
    tenant_id: str,
    query: str,
    collection_id: Optional[str] = None,
    collection_name: Optional[str] = None,
    source_ids: Optional[List[str]] = None,
): 
    """Ejecuta MAS Simple por tenant/colección usando caso de uso."""
    scope_label = f"tenant={tenant_id}"
    if collection_name:
        scope_label += f" / colección={collection_name}"
    elif collection_id:
        scope_label += f" / colección={collection_id}"

    print(f"\n⚙️  Motor MAS Simple Híbrido Activado para: '{scope_label}'")
    print(f"🔍 Analizando: '{query}'...")
    retriever = RetrievalToolsAdapter(
        tools=tools,
        collection_name=collection_name,
        allowed_source_ids=set(source_ids or []),
    )
    answer_generator = GroqAnswerGeneratorAdapter(client=client, model_name=MODEL_NAME)
    validator = LiteralEvidenceValidator()
    use_case = HandleQuestionUseCase(
        retriever=retriever,
        answer_generator=answer_generator,
        validator=validator,
    )

    result = await use_case.execute(
        HandleQuestionCommand(
            query=query,
            tenant_id=tenant_id,
            collection_id=collection_id,
            scope_label=scope_label,
        )
    )

    print(f"🧭 Modo de consulta: {result.intent.mode}")
    chunk_count = sum(1 for ev in result.answer.evidence if ev.source.startswith("C"))
    summary_count = sum(1 for ev in result.answer.evidence if ev.source.startswith("R"))
    print(f"✅ Recuperación: {chunk_count} fragmentos de detalle, {summary_count} nodos RAPTOR.")
    if not result.validation.accepted:
        print("⚠️ Validación MAS Simple: " + "; ".join(result.validation.issues))
    answer = result.answer.text

    print("\n" + "=" * 60)
    print(f"🤖 RESPUESTA ({scope_label})")
    print("=" * 60)
    print(answer)
    print("=" * 60 + "\n")


# ─────────────────────────────────────────────────────────────────────────────
# CLI Principal
# ─────────────────────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="RAG chat por tenant/carpeta")
    parser.add_argument("--tenant-id", help="Tenant institucional")
    parser.add_argument("--collection-id", help="ID de carpeta/colección")
    parser.add_argument("--collection-name", help="Nombre de carpeta/colección")
    parser.add_argument("--no-multi-hop", action="store_true", help="Desactiva cruce entre múltiples documentos")
    return parser.parse_args()


async def run_tenant_chat(
    tenant_id: str,
    collection_id: Optional[str],
    collection_name: Optional[str],
    no_multi_hop: bool = False,
) -> None:
    docs = await list_documents_for_scope(
        tenant_id=tenant_id,
        collection_id=collection_id,
        collection_name=collection_name,
    )
    if not docs:
        print("⚠️ No hay documentos para el tenant/carpeta seleccionados.")
        return

    display_collection = collection_name or collection_id or "todo"
    print("🚀 Inicializando Chat MAS Simple por Tenant")
    print(f"🏢 Tenant: {tenant_id}")
    print(f"📁 Carpeta/Colección: {display_collection}")
    print(f"📚 Documentos en scope: {len(docs)}")

    source_ids = [str(d.get("id")) for d in docs if d.get("id")]
    filtered_source_ids: Optional[List[str]] = None
    if no_multi_hop and source_ids:
        filtered_source_ids = [source_ids[0]]
        print(f"🔒 Modo single-doc activo (no-multi-hop). Fuente: {filtered_source_ids[0]}")
    repo = SupabaseRetrievalRepository()
    tools = RetrievalTools(repository=repo)

    print("\n💡 Escribe tu pregunta (o 'salir')")
    while True:
        try:
            query = input("❓ > ").strip()
            if query.lower() in ["salir", "exit", "quit"]:
                print("Okey, ¡adiós! 👋")
                break
            if not query:
                continue

            await query_engine(
                tools=tools,
                tenant_id=tenant_id,
                query=query,
                collection_id=collection_id,
                collection_name=collection_name,
                source_ids=filtered_source_ids,
            )

        except KeyboardInterrupt:
            break
        except Exception as e:
            print(f"❌ Error: {e}")


async def main():
    args = parse_args()
    if not args.tenant_id:
        print("❌ Debes indicar --tenant-id para usar este CLI.")
        print("💡 Usa ./chat.sh para seleccionar tenant/carpeta interactivamente.")
        return

    await run_tenant_chat(
        tenant_id=args.tenant_id,
        collection_id=args.collection_id,
        collection_name=args.collection_name,
        no_multi_hop=args.no_multi_hop,
    )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n\n👋 Chat interrumpido por el usuario. Saliendo...")
        sys.exit(0)
