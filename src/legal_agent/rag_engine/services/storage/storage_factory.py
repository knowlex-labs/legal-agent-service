from .storage_interface import StorageServiceInterface
from .local_storage_service import LocalStorageService


def get_storage_service() -> StorageServiceInterface:
    """
    Returns a storage service instance. MinIO support has been removed.
    Always returns LocalStorageService regardless of config.
    """
    # Always use local storage (MinIO has been removed)
    return LocalStorageService()