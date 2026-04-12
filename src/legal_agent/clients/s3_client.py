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
        loop = asyncio.get_running_loop()

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

    async def upload_bytes(self, s3_path: str, data: bytes, content_type: str) -> str:
        """Upload raw bytes to S3. Returns s3_path."""
        loop = asyncio.get_running_loop()

        def _upload():
            self._client.put_object(
                Bucket=self._bucket_name,
                Key=s3_path,
                Body=data,
                ContentType=content_type,
            )
            logger.info(f"[s3] Uploaded {len(data)} bytes to s3://{self._bucket_name}/{s3_path}")

        await loop.run_in_executor(None, _upload)
        return s3_path

    async def download_bytes(self, s3_path: str) -> bytes:
        """Download an S3 object and return its raw bytes."""
        import gzip

        loop = asyncio.get_running_loop()

        def _download():
            response = self._client.get_object(Bucket=self._bucket_name, Key=s3_path)
            raw = response["Body"].read()
            encoding = response.get("ContentEncoding", "")
            content_type = response.get("ContentType", "")
            logger.info(
                f"[s3] Downloaded {len(raw)} bytes from {s3_path} "
                f"| ContentType={content_type} | ContentEncoding={encoding} "
                f"| first20={raw[:20].hex()}"
            )
            if encoding == "gzip":
                raw = gzip.decompress(raw)
                logger.info(f"[s3] Decompressed gzip: {len(raw)} bytes")
            return raw

        return await loop.run_in_executor(None, _download)

    async def signed_url(self, s3_path: str) -> str:
        """Generate a presigned GET URL for the given S3 key."""
        loop = asyncio.get_running_loop()

        def _sign():
            return self._client.generate_presigned_url(
                "get_object",
                Params={"Bucket": self._bucket_name, "Key": s3_path},
                ExpiresIn=self._expiry,
            )

        return await loop.run_in_executor(None, _sign)
