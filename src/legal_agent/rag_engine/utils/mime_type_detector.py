"""
MIME type detection utility for file content serving.
"""

import os
from typing import Dict

# MIME type mapping for supported file types
MIME_TYPE_MAP: Dict[str, str] = {
    '.pdf': 'application/pdf',
    '.txt': 'text/plain; charset=utf-8',
    '.md': 'text/markdown; charset=utf-8',
    '.csv': 'text/csv; charset=utf-8',
    '.json': 'application/json',
    '.xml': 'application/xml',
    '.html': 'text/html; charset=utf-8',
    '.htm': 'text/html; charset=utf-8',
}

DEFAULT_MIME_TYPE = 'application/octet-stream'


def get_mime_type(file_path: str) -> str:
    """
    Get MIME type for a file based on its extension.

    Args:
        file_path: Path to the file (can include storage prefix)

    Returns:
        MIME type string
    """
    # Extract filename from storage path if needed
    if file_path.startswith('local://'):
        file_path = file_path[len('local://'):]
    elif '/' in file_path:
        file_path = os.path.basename(file_path)

    # Get file extension
    _, ext = os.path.splitext(file_path.lower())

    return MIME_TYPE_MAP.get(ext, DEFAULT_MIME_TYPE)


def get_content_disposition_filename(file_path: str) -> str:
    """
    Extract filename for Content-Disposition header.

    Args:
        file_path: Path to the file (can include storage prefix)

    Returns:
        Clean filename for Content-Disposition header
    """
    # Extract filename from storage path if needed
    if file_path.startswith('local://'):
        file_path = file_path[len('local://'):]

    filename = os.path.basename(file_path)

    # Remove file_id prefix if present (format: uuid_filename)
    if '_' in filename:
        parts = filename.split('_', 1)
        if len(parts) == 2:
            try:
                uuid.UUID(parts[0])
                filename = parts[1]
            except ValueError:
                # Not a UUID prefix, do nothing.
                pass

    return filename