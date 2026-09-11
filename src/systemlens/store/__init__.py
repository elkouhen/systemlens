"""Compatibility facade for the SQLite store implementation."""

from systemlens.storage.sqlite import CodeChunk, Store, StoreError

__all__ = ["CodeChunk", "Store", "StoreError"]
