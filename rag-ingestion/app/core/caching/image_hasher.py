"""Deterministic image fingerprinting utilities for visual-cache keys."""

from __future__ import annotations

import hashlib
from pathlib import Path


class ImageHasher:
    """SHA-256 hasher for binary image payloads."""

    @staticmethod
    def sha256_bytes(image_bytes: bytes) -> str:
        """Return a deterministic SHA-256 hex digest for image bytes."""

        if not image_bytes:
            raise ValueError("image_bytes cannot be empty for hashing.")

        digest = hashlib.sha256()
        digest.update(image_bytes)
        return digest.hexdigest()

    @staticmethod
    def sha256_file(file_path: str | Path) -> str:
        """Hash a file by content (not by filename/path)."""

        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"Image file not found: {path}")

        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)

        return digest.hexdigest()
