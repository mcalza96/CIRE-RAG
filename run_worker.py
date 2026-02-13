
import asyncio
import os
import sys
from dotenv import load_dotenv

# Ensure app module is in path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from app.worker import IngestionWorker
from app.infrastructure.supabase.client import find_and_load_env

async def main():
    print("🚀 [Loader] Inicializando Worker de Ingesta RAG...")
    
    # Load env vars using shared logic (finds .env.local in root)
    find_and_load_env()
    
    # Validate critical env vars (Handle NEXT_PUBLIC prefix)
    url = os.getenv("SUPABASE_URL") or os.getenv("NEXT_PUBLIC_SUPABASE_URL")
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    
    if not url or not key:
        print(f"❌ [Loader] Faltan variables de entorno: {'SUPABASE_URL' if not url else ''} {'SUPABASE_SERVICE_ROLE_KEY' if not key else ''}")
        sys.exit(1)
        
    worker = IngestionWorker()
    
    try:
        await worker.start()
    except KeyboardInterrupt:
        print("\n🛑 [Loader] Worker detenido por el usuario.")
    except Exception as e:
        print(f"❌ [Loader] Error crítico no controlado: {e}")
        # En producción, aquí podríamos reiniciar el proceso o alertar
        sys.exit(1)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
