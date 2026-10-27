from storage3 import AsyncStorageClient

from app.config import Settings

# Service-role key bypasses RLS, so this client is storage-only and must never be used for auth or DB calls.
_storage_clients: dict[str, AsyncStorageClient] = {}


def get_storage_client(settings: Settings) -> AsyncStorageClient | None:
    """Return a cached service-role storage client, or None when the key is not configured (fail closed)."""
    key = settings.SUPABASE_SERVICE_ROLE_KEY
    if not settings.SUPABASE_URL or not key:
        return None
    cache_key = f"{settings.SUPABASE_URL}|{key[-6:]}"
    if cache_key not in _storage_clients:
        _storage_clients[cache_key] = AsyncStorageClient(
            f"{settings.SUPABASE_URL.rstrip('/')}/storage/v1",
            {"apikey": key, "Authorization": f"Bearer {key}"},
        )
    return _storage_clients[cache_key]
