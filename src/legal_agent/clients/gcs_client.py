"""Google Cloud Storage client wrapper."""

import asyncio
import datetime
import logging

from google.cloud import storage
from google.oauth2 import service_account

from legal_agent.config import Settings

logger = logging.getLogger(__name__)


class GCSClient:
    def __init__(self, settings: Settings):
        self._bucket_name = settings.gcs_bucket
        self._expiry = settings.gcs_signed_url_expiry

        if settings.gcs_service_account_json:
            self._signing_credentials: service_account.Credentials | None = (
                service_account.Credentials.from_service_account_file(
                    settings.gcs_service_account_json,
                    scopes=["https://www.googleapis.com/auth/cloud-platform"],
                )
            )
            self._client = storage.Client(credentials=self._signing_credentials)
        else:
            self._client = storage.Client()
            self._signing_credentials = None

        self._bucket = self._client.bucket(self._bucket_name)

    async def upload_text(self, gcs_path: str, content: str) -> str:
        """Upload text content to GCS. Returns gcs_path."""
        loop = asyncio.get_event_loop()

        def _upload():
            blob = self._bucket.blob(gcs_path)
            blob.upload_from_string(content, content_type="text/markdown; charset=utf-8")
            logger.info(f"[gcs] Uploaded gs://{self._bucket_name}/{gcs_path}")

        await loop.run_in_executor(None, _upload)
        return gcs_path

    async def signed_url(self, gcs_path: str) -> str:
        """Generate a v4 signed URL for the given GCS path."""
        loop = asyncio.get_event_loop()
        expiration = datetime.timedelta(seconds=self._expiry)
        signing_creds = self._signing_credentials

        def _sign():
            blob = self._bucket.blob(gcs_path)
            return blob.generate_signed_url(
                version="v4",
                expiration=expiration,
                method="GET",
                credentials=signing_creds,
            )

        url = await loop.run_in_executor(None, _sign)
        return url
