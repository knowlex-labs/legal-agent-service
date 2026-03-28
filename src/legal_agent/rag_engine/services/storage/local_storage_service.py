import os
import logging
from typing import Optional, Iterator, Tuple
from .storage_interface import StorageServiceInterface
from legal_agent.rag_engine.utils.mime_type_detector import get_mime_type

logger = logging.getLogger(__name__)


class LocalStorageService(StorageServiceInterface):

    def download_for_processing(self, storage_path: str) -> Optional[str]:
        try:
            local_path = storage_path[8:]  # Remove "local://" prefix
            if os.path.exists(local_path):
                return local_path
            else:
                logger.error(f"Local file not found: {local_path}")
                return None
        except Exception as e:
            logger.error(f"Failed to get local file for processing: {e}")
            return None

    def upload_file(self, file_data: bytes, storage_path: str) -> bool:
        try:
            local_path = storage_path[8:]  # Remove "local://" prefix
            os.makedirs(os.path.dirname(local_path), exist_ok=True)
            with open(local_path, "wb") as f:
                f.write(file_data)
            return True
        except Exception as e:
            logger.error(f"Failed to upload file locally: {e}")
            return False

    def delete_file(self, storage_path: str) -> bool:
        try:
            local_path = storage_path[8:]  # Remove "local://" prefix
            os.remove(local_path)
            return True
        except Exception as e:
            logger.error(f"Failed to delete local file: {e}")
            return False

    def exists(self, storage_path: str) -> bool:
        try:
            local_path = storage_path[8:]  # Remove "local://" prefix
            return os.path.exists(local_path)
        except Exception:
            return False

    def get_file_url(self, storage_path: str) -> str:
        return storage_path

    def stream_file(self, storage_path: str) -> Iterator[bytes]:
        """Stream file content in chunks for efficient handling of large files."""
        try:
            local_path = storage_path[8:]  # Remove "local://" prefix

            if not os.path.exists(local_path):
                logger.error(f"Local file not found for streaming: {local_path}")
                return iter([])

            chunk_size = 8192  # 8KB chunks
            with open(local_path, "rb") as f:
                while chunk := f.read(chunk_size):
                    yield chunk

        except Exception as e:
            logger.error(f"Failed to stream local file: {e}")
            return iter([])

    def get_content_type_and_size(self, storage_path: str) -> Tuple[str, int]:
        """Get MIME content type and file size for HTTP headers."""
        try:
            local_path = storage_path[len("local://"):]  # Remove "local://" prefix

            if not os.path.exists(local_path):
                logger.error(f"Local file not found for content type detection: {local_path}")
                return "application/octet-stream", 0

            # Get file size
            file_size = os.path.getsize(local_path)

            # Get MIME type
            mime_type = get_mime_type(storage_path)

            return mime_type, file_size

        except Exception as e:
            logger.error(f"Failed to get content type and size for local file: {e}")
            return "application/octet-stream", 0