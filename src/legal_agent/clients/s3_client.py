"""Amazon S3 storage client wrapper."""

import asyncio
import logging

import boto3

from legal_agent.config import Settings

logger = logging.getLogger(__name__)


class S3Client:
    def __init__(self, settings: Settings):
        self._bucket_name = settings.s3_bucket_name
        self._expiry = settings.s3_signed_url_expiry
        self._client = boto3.client(
            "s3",
            aws_access_key_id=settings.s3_access_key,
            aws_secret_access_key=settings.s3_secret_key,
            region_name=settings.s3_region_name,
        )

    async def upload_text(self, s3_path: str, content: str) -> str:
        """Upload UTF-8 text to S3. Returns s3_path."""
        loop = asyncio.get_event_loop()

        def _upload():
            self._client.put_object(
                Bucket=self._bucket_name,
                Key=s3_path,
                Body=content.encode("utf-8"),
                ContentType="text/markdown; charset=utf-8",
            )
            logger.info(f"[s3] Uploaded s3://{self._bucket_name}/{s3_path}")

        await loop.run_in_executor(None, _upload)
        return s3_path

    async def signed_url(self, s3_path: str) -> str:
        """Generate a presigned GET URL for the given S3 key."""
        loop = asyncio.get_event_loop()

        def _sign():
            return self._client.generate_presigned_url(
                "get_object",
                Params={"Bucket": self._bucket_name, "Key": s3_path},
                ExpiresIn=self._expiry,
            )

        return await loop.run_in_executor(None, _sign)
