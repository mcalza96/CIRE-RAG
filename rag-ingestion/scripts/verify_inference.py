"""
Verification script for the Inference Module.
Tests provider instantiation and the factory.
"""

import asyncio
import os
import sys

# Add the project root to sys.path if needed
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../..")))

from app.core.inference import create_stream_provider, InferConfig, IStreamProvider

async def main():
    print("--- Inference Module Verification ---")
    
    # 1. Test Factory - Mock
    print("\n[1] Testing Factory (Mock)...")
    try:
        provider = create_stream_provider("mock", response_text="Test response")
        print(f"✓ Created provider: {type(provider).__name__}")
        
        config = InferConfig()
        messages = [{"role": "user", "content": "Hello"}]
        
        print("Streaming from mock:")
        async for chunk in provider.stream_completion(messages, config):
            print(f"  {chunk.strip()}")
            
    except Exception as e:
        print(f"✗ Mock provider failed: {e}")

    # 2. Test Factory - Groq (if key available)
    print("\n[2] Testing Factory (Groq Pre-check)...")
    if os.environ.get("GROQ_API_KEY"):
        try:
            provider = create_stream_provider("groq")
            print(f"✓ Created provider: {type(provider).__name__}")
        except Exception as e:
            print(f"✗ Groq provider creation failed: {e}")
    else:
        print("! GROQ_API_KEY not found, skipping instantiation test.")

    # 3. Test Factory - Gemini (if key available)
    print("\n[3] Testing Factory (Gemini Pre-check)...")
    if os.environ.get("GEMINI_API_KEY"):
        try:
            provider = create_stream_provider("gemini")
            print(f"✓ Created provider: {type(provider).__name__}")
        except Exception as e:
            print(f"✗ Gemini provider creation failed: {e}")
    else:
        print("! GEMINI_API_KEY not found, skipping instantiation test.")

    # 4. Test Auto-detection
    print("\n[4] Testing Auto-detection...")
    try:
        provider = create_stream_provider("auto")
        print(f"✓ Auto-detected provider: {type(provider).__name__}")
    except Exception as e:
        print(f"✗ Auto-detection failed: {e}")

    print("\n--- Verification Complete ---")

if __name__ == "__main__":
    asyncio.run(main())
