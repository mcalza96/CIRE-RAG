import os
import asyncio
import uuid
import logging
import time
from typing import Dict, Any
from dotenv import load_dotenv
from supabase import create_client, Client

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")

if not SUPABASE_URL or not SUPABASE_KEY:
    logger.error("❌ SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY not found in .env")
    exit(1)

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

async def verify_integration():
    test_id = str(uuid.uuid4())
    filename = f"test_integration_{test_id[:8]}.txt"
    storage_path = f"tests/integration/{filename}"
    local_fixture = "tests/fixtures/test_doc.txt"

    if not os.path.exists(local_fixture):
        logger.error(f"❌ Fixture not found: {local_fixture}")
        return

    # 1. Upload file to Supabase Storage
    logger.info(f"⏳ Subiendo archivo a storage: {storage_path}...")
    try:
        with open(local_fixture, "rb") as f:
            supabase.storage.from_("private_assets").upload(
                path=storage_path,
                file=f,
                file_options={"upsert": "true", "content-type": "text/plain"}
            )
        logger.info("✅ Archivo subido con éxito.")
    except Exception as e:
        logger.error(f"❌ Error subiendo archivo: {e}")
        return

    # 2. Insert record into source_documents
    # We use high-level metadata to trigger the correct strategy (CONTENT)
    logger.info(f"🚀 Insertando registro en source_documents con ID: {test_id}...")
    
    # Metadata used by the worker to resolve strategies and context
    metadata = {
        "title": f"Test Document {test_id[:8]}",
        "typeId": "4073d1fd-8580-4576-8e47-84ac67b90db8",  # 'Contenido' node UUID
        "status": "queued",
        "retry_count": 0,
        "is_global": True,
        "storage_path": storage_path
    }

    try:
        supabase.table("source_documents").insert({
            "id": test_id,
            "filename": filename,
            "storage_path": storage_path,
            "status": "queued",
            "metadata": metadata,
            "is_global": True
        }).execute()
        logger.info("✅ Registro insertado exitosamente.")
    except Exception as e:
        logger.error(f"❌ Error insertando registro: {e}")
        return

    # 3. Polling for Status Changes
    logger.info("⏳ Esperando a que el worker procese el documento...")
    
    max_attempts = 30 # 60 seconds total
    processed = False
    
    for attempt in range(max_attempts):
        await asyncio.sleep(2)
        try:
            res = supabase.table("source_documents").select("status, metadata").eq("id", test_id).single().execute()
            status = res.data.get("status")
            meta = res.data.get("metadata", {})
            meta_status = meta.get("status")
            
            logger.info(f"[{attempt+1}/{max_attempts}] Status actual: DB='{status}', Meta='{meta_status}'")
            
            if status in ["ready", "processed"] or meta_status in ["ready", "processed"]:
                logger.info("✅ ¡Ingesta completada con éxito!")
                processed = True
                break
            elif status in ["error", "dead_letter", "failed"] or meta_status in ["error", "dead_letter", "failed"]:
                logger.error(f"❌ El worker falló: {meta.get('last_error') or 'Status Error'}")
                return
        except Exception as e:
            logger.warning(f"⚠️ Error consultando status: {e}")

    if not processed:
        logger.error("❌ Tiempo de espera agotado. El worker no procesó el documento.")
        return

    # 4. Validation of Results (Content Chunks)
    logger.info("🔍 Validando persistencia de fragmentos...")
    try:
        res = supabase.table("content_chunks").select("count", count="exact").eq("source_id", test_id).execute()
        count = res.count
        if count > 0:
            logger.info(f"✅ Se encontraron {count} fragmentos persistidos correctamente.")
            logger.info("✨ PRUEBA DE INTEGRACIÓN EXITOSA (10/10) ✨")
        else:
            logger.error("❌ No se encontraron fragmentos para este documento.")
    except Exception as e:
        logger.error(f"❌ Error validando fragmentos: {e}")

if __name__ == "__main__":
    asyncio.run(verify_integration())
