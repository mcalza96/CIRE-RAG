"""
doc_chat_cli.py — CLI de Auditoría Documental con Recuperación Híbrida
======================================================================
Estrategia Dual:
  1. Vector Search (Chunks específicos del documento, filtrados por source_id)
  2. RAPTOR Summaries (Resúmenes jerárquicos de alto nivel)

Ambas consultas se disparan en paralelo con asyncio.gather para máxima velocidad.
"""

import asyncio
import json
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
from app.core.ai_models import AIModelConfig
from app.infrastructure.supabase.client import get_async_supabase_client
from app.qa_orchestrator.application import HandleQuestionCommand, HandleQuestionResult, HandleQuestionUseCase
from app.qa_orchestrator.infrastructure.adapters import (
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
    print("❌ Falta GROQ_API_KEY para iniciar Q/A Orchestrator.")
    print("💡 Define GROQ_API_KEY en variables de entorno o en .env.local del repo.")
    sys.exit(1)

MODEL_NAME = AIModelConfig.get_groq_model_for_capability("CHAT")

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
        rows = res.data if isinstance(res.data, list) else []
        return [row for row in rows if isinstance(row, dict)]
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
# Motor Q/A Orchestrator
# ─────────────────────────────────────────────────────────────────────────────

async def query_engine(
    tools: RetrievalTools,
    tenant_id: str,
    query: str,
    collection_id: Optional[str] = None,
    collection_name: Optional[str] = None,
    source_ids: Optional[List[str]] = None,
    session_state: Optional[Dict[str, Any]] = None,
) -> HandleQuestionResult:
    """Ejecuta Q/A Orchestrator por tenant/colección usando caso de uso."""
    scope_label = f"tenant={tenant_id}"
    if collection_name:
        scope_label += f" / colección={collection_name}"
    elif collection_id:
        scope_label += f" / colección={collection_id}"

    print(f"\n⚙️  Motor Q/A Orchestrator Híbrido Activado para: '{scope_label}'")
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

    effective_query = query
    if session_state:
        resolved_scopes = session_state.get("resolved_scopes") or []
        analysis_mode = str(session_state.get("analysis_mode") or "").strip()
        memory_notes: list[str] = []
        if isinstance(resolved_scopes, list) and resolved_scopes:
            memory_notes.append("Scopes confirmados en sesión: " + ", ".join(str(v) for v in resolved_scopes))
        if analysis_mode:
            memory_notes.append("Modo preferido en sesión: " + analysis_mode)
        if memory_notes:
            effective_query = query + "\n\n" + "\n".join(memory_notes)

    result = await use_case.execute(
        HandleQuestionCommand(
            query=effective_query,
            tenant_id=tenant_id,
            collection_id=collection_id,
            scope_label=scope_label,
        )
    )

    print(f"🧭 Modo de consulta: {result.intent.mode}")
    if result.plan.requested_standards:
        print("🧷 Scope detectado: " + ", ".join(result.plan.requested_standards))
    elif result.intent.mode == "ambigua_scope":
        print("🧷 Scope detectado: ambiguo (falta norma explícita)")
    chunk_count = sum(1 for ev in result.answer.evidence if ev.source.startswith("C"))
    summary_count = sum(1 for ev in result.answer.evidence if ev.source.startswith("R"))
    print(f"✅ Recuperación: {chunk_count} fragmentos de detalle, {summary_count} nodos RAPTOR.")
    if result.clarification:
        print("🧠 Clarificación requerida: " + result.clarification.question)
        if result.clarification.options:
            print("🧩 Opciones: " + " | ".join(result.clarification.options))
    if not result.validation.accepted:
        print("⚠️ Validación Q/A Orchestrator: " + "; ".join(result.validation.issues))
    answer = result.answer.text

    print("\n" + "=" * 60)
    print(f"🤖 RESPUESTA ({scope_label})")
    print("=" * 60)
    print(answer)
    print("=" * 60 + "\n")
    return result


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
    async def _interpret_clarification_answer(answer: str, options: tuple[str, ...] | tuple[str] | tuple) -> Dict[str, Any]:
        text = (answer or "").strip()
        lowered = text.lower()

        scopes: list[str] = []
        for code in ("9001", "14001", "45001"):
            if code in lowered:
                scopes.append(f"ISO {code}")

        if "conserv" in lowered or "denunc" in lowered or "repres" in lowered:
            return {"analysis_mode": "Protección al denunciante", "resolved_scopes": scopes}
        if "forens" in lowered or "trazab" in lowered:
            return {"analysis_mode": "Forense de trazabilidad", "resolved_scopes": scopes}
        if "balanc" in lowered or "integr" in lowered or "trinorma" in lowered or "confirmo" in lowered:
            if not scopes:
                scopes = ["ISO 9001", "ISO 14001", "ISO 45001"]
            return {"analysis_mode": "Balanceado trinorma", "resolved_scopes": scopes}

        options_list = [str(opt) for opt in (options or ())]
        if options_list:
            system_prompt = (
                "Clasifica la respuesta de aclaración de un usuario. "
                "Devuelve JSON estricto con llaves: analysis_mode, resolved_scopes. "
                "analysis_mode debe ser uno de: Protección al denunciante, Forense de trazabilidad, Balanceado trinorma. "
                "resolved_scopes debe ser lista con ISO 9001/ISO 14001/ISO 45001 si aplica."
            )
            user_prompt = (
                f"Opciones ofrecidas: {', '.join(options_list)}\n"
                f"Respuesta usuario: {text}"
            )
            try:
                completion = await asyncio.to_thread(
                    client.chat.completions.create,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    model=MODEL_NAME,
                    temperature=0,
                )
                raw = str(completion.choices[0].message.content or "").strip()
                payload = json.loads(raw)
                mode = str(payload.get("analysis_mode") or "").strip()
                if mode not in {"Protección al denunciante", "Forense de trazabilidad", "Balanceado trinorma"}:
                    mode = "Balanceado trinorma"
                out_scopes = payload.get("resolved_scopes")
                resolved_scopes = [
                    str(item).strip()
                    for item in (out_scopes if isinstance(out_scopes, list) else [])
                    if isinstance(item, str) and str(item).strip()
                ]
                return {"analysis_mode": mode, "resolved_scopes": resolved_scopes}
            except Exception:
                pass

        return {"analysis_mode": "Balanceado trinorma", "resolved_scopes": scopes}

    def _rewrite_with_clarification(original_query: str, interpretation: Dict[str, Any]) -> str:
        mode = str(interpretation.get("analysis_mode") or "Balanceado trinorma").strip()
        scopes = interpretation.get("resolved_scopes")
        resolved_scopes = [str(item) for item in scopes] if isinstance(scopes, list) else []

        suffix_lines = [f"Aclaración de alcance: {mode}."]
        if resolved_scopes:
            suffix_lines.append("Normas confirmadas: " + ", ".join(resolved_scopes) + ".")
        return f"{original_query}\n\n" + " ".join(suffix_lines)

    docs = await list_documents_for_scope(
        tenant_id=tenant_id,
        collection_id=collection_id,
        collection_name=collection_name,
    )
    if not docs:
        print("⚠️ No hay documentos para el tenant/carpeta seleccionados.")
        return

    display_collection = collection_name or collection_id or "todo"
    print("🚀 Inicializando Chat Q/A Orchestrator por Tenant")
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
    session_state: Dict[str, Any] = {}

    print("\n💡 Escribe tu pregunta (o 'salir')")
    while True:
        try:
            query = input("❓ > ").strip()
            if query.lower() in ["salir", "exit", "quit"]:
                print("Okey, ¡adiós! 👋")
                break
            if query.lower() in ["/reset", "reset", "limpiar memoria", "clear"]:
                session_state.clear()
                print("🧹 Memoria de sesión limpiada.")
                continue
            if not query:
                continue

            result = await query_engine(
                tools=tools,
                tenant_id=tenant_id,
                query=query,
                collection_id=collection_id,
                collection_name=collection_name,
                source_ids=filtered_source_ids,
                session_state=session_state,
            )

            if result.clarification:
                clarification_answer = input("📝 Aclaración > ").strip()
                if clarification_answer:
                    interpretation = await _interpret_clarification_answer(
                        clarification_answer,
                        result.clarification.options,
                    )
                    session_state.update(interpretation)
                    clarified_query = _rewrite_with_clarification(query, interpretation)
                    await query_engine(
                        tools=tools,
                        tenant_id=tenant_id,
                        query=clarified_query,
                        collection_id=collection_id,
                        collection_name=collection_name,
                        source_ids=filtered_source_ids,
                        session_state=session_state,
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
