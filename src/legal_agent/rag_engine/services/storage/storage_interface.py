from abc import ABC, abstractmethod
from typing import Optional, Iterator, Tuple


class StorageServiceInterface(ABC):

    @abstractmethod
    def download_for_processing(self, storage_path: str) -> Optional[str]:
        pass

    @abstractmethod
    def upload_file(self, file_data: bytes, storage_path: str) -> bool:
        pass

    @abstractmethod
    def delete_file(self, storage_path: str) -> bool:
        pass

    @abstractmethod
    def exists(self, storage_path: str) -> bool:
        pass

    @abstractmethod
    def get_file_url(self, storage_path: str) -> str:
        pass

    @abstractmethod
    def stream_file(self, storage_path: str) -> Iterator[bytes]:
        """Stream file content in chunks for efficient handling of large files."""
        pass

    @abstractmethod
    def get_content_type_and_size(self, storage_path: str) -> Tuple[str, int]:
        """Get MIME content type and file size for HTTP headers."""
        pass