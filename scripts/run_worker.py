import asyncio
import os
import sys

# Ensure app module is in path
# Ensure project root is in path (one level up from scripts/)
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.worker import IngestionWorker
from app.core.settings import settings


async def main():
    print("🚀 [Loader] Inicializando Worker de Ingesta RAG...")

    # Settings loads env from repo-root .env/.env.local deterministically.
    url = settings.SUPABASE_URL
    key = settings.SUPABASE_SERVICE_KEY
    if not url or not key:
        missing = []
        if not url:
            missing.append("SUPABASE_URL")
        if not key:
            missing.append("SUPABASE_SERVICE_ROLE_KEY")
        print(f"❌ [Loader] Faltan variables de entorno: {' '.join(missing)}")
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
