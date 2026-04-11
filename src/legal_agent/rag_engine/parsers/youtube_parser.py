
import re
import logging
from typing import List, Optional
from pathlib import Path
from urllib.parse import urlparse, parse_qs

from .base_parser import BaseParser
from .models import ParsedContent, ParsedMetadata, ContentSection

logger = logging.getLogger(__name__)

class YouTubeParser(BaseParser):
    def __init__(self, gemini_api_key: Optional[str] = None):
        super().__init__()
        self.gemini_api_key = gemini_api_key

        self.youtube_patterns = [
            r'(?:https?://)?(?:www\.)?youtube\.com/watch\?v=([a-zA-Z0-9_-]{11})',
            r'(?:https?://)?(?:www\.)?youtu\.be/([a-zA-Z0-9_-]{11})',
            r'(?:https?://)?(?:www\.)?youtube\.com/embed/([a-zA-Z0-9_-]{11})',
        ]

        from youtube_transcript_api import YouTubeTranscriptApi
        self.ytt_api = YouTubeTranscriptApi()

    def can_handle(self, source: str | Path) -> bool:
        if isinstance(source, Path):
            return False

        video_id = self._extract_video_id(source)
        return video_id is not None

    def parse(self, source: str | Path) -> ParsedContent:
        if isinstance(source, Path):
            raise ValueError(f"YouTube parser requires a URL, not a file path: {source}")

        self.validate_source(source)

        video_id = self._extract_video_id(source)
        if not video_id:
            raise ValueError(f"Invalid YouTube URL: {source}")

        transcript = self._get_transcript(video_id, source)
        metadata = self._extract_metadata(video_id, source)
        full_text = "\n".join([entry['text'] for entry in transcript])
        sections = self._build_timestamp_sections(transcript)

        return ParsedContent(
            text=full_text,
            metadata=metadata,
            sections=sections,
            source_type='youtube',
            has_equations=False,
            has_diagrams=False,
            has_code_blocks=False
        )

    def _extract_video_id(self, url: str) -> Optional[str]:
        for pattern in self.youtube_patterns:
            match = re.search(pattern, url)
            if match:
                return match.group(1)

        parsed = urlparse(url)
        if 'youtube.com' in parsed.netloc:
            query = parse_qs(parsed.query)
            if 'v' in query:
                return query['v'][0]

        return None

    def _get_transcript(self, video_id: str, url: str) -> List[dict]:
        try:
            transcript_obj = self.ytt_api.fetch(video_id)
            transcript = [
                {
                    'text': snippet.text,
                    'start': snippet.start,
                    'duration': snippet.duration
                }
                for snippet in transcript_obj
            ]
            return transcript

        except Exception as e:
            logger.error(f"youtube-transcript-api failed for {video_id}: {e}")
            if self.gemini_api_key:
                return self._transcribe_with_gemini(url, video_id)
            else:
                raise ValueError(f"Failed to get transcript for {video_id}. youtube-transcript-api failed and no Gemini API key provided for fallback.")

    def _transcribe_with_gemini(self, url: str, video_id: str) -> List[dict]:
        try:
            from legal_agent.rag_engine.utils.llm_client import LlmClient
            import json

            llm_client = LlmClient()

            prompt = f"""
            Please transcribe the YouTube video at this URL: {url}

            Return the transcription in JSON format with timestamps:
            {{
                "transcript": [
                    {{"text": "segment text", "start": 0.0, "duration": 5.0}},
                    ...
                ]
            }}

            Break the transcript into natural segments (paragraphs or sentences) with approximate timestamps.
            """

            response = llm_client.generate_answer(
                query=prompt,
                context_chunks=[],
                force_json=True
            )

            try:
                data = json.loads(response)
                transcript = data.get('transcript', [])

                if not transcript:
                    raise ValueError("Gemini returned empty transcript")

                return transcript

            except json.JSONDecodeError as json_e:
                logger.error(f"Failed to parse Gemini JSON response: {json_e}")
                raise ValueError(f"Failed to parse Gemini JSON response and YouTube transcript API also failed for {video_id}")

        except Exception as e:
            logger.error(f"Gemini transcription failed for {video_id}: {e}")
            raise ValueError(f"All transcription methods failed for {video_id}: {e}")

    def _extract_metadata(self, video_id: str, url: str) -> ParsedMetadata:
        try:
            from pytube import YouTube

            yt = YouTube(url)

            title = yt.title
            channel = yt.author
            duration_seconds = yt.length

            hours = duration_seconds // 3600
            minutes = (duration_seconds % 3600) // 60
            seconds = duration_seconds % 60

            if hours > 0:
                duration = f"{hours:02d}:{minutes:02d}:{seconds:02d}"
            else:
                duration = f"{minutes:02d}:{seconds:02d}"

            return ParsedMetadata(
                title=title,
                url=url,
                duration=duration,
                channel=channel,
                video_id=video_id
            )

        except Exception as e:
            logger.warning(f"Failed to extract metadata for {video_id}: {e}")

            return ParsedMetadata(
                title=f"YouTube Video {video_id}",
                url=url,
                duration=None,
                channel=None,
                video_id=video_id
            )

    def _build_timestamp_sections(self, transcript: List[dict]) -> List[ContentSection]:
        sections = []

        if not transcript:
            return sections

        current_section_text = ""
        current_section_start = 0.0
        section_duration = 0.0
        target_duration = 90.0

        for entry in transcript:
            text = entry['text']
            start = entry['start']
            duration = entry.get('duration', 0.0)

            if not current_section_text:
                current_section_start = start
                current_section_text = text
                section_duration = duration
            else:
                current_section_text += " " + text
                section_duration += duration

                if section_duration >= target_duration:
                    timestamp = self._format_timestamp(current_section_start)

                    sections.append(ContentSection(
                        level=2,
                        text=current_section_text.strip(),
                        title=None,
                        parent_id=None,
                        page_number=None,
                        timestamp=timestamp,
                        section_id=f"timestamp_{timestamp}"
                    ))

                    current_section_text = ""
                    section_duration = 0.0

        if current_section_text.strip():
            timestamp = self._format_timestamp(current_section_start)

            sections.append(ContentSection(
                level=2,
                text=current_section_text.strip(),
                title=None,
                parent_id=None,
                page_number=None,
                timestamp=timestamp,
                section_id=f"timestamp_{timestamp}"
            ))

        return sections

    def _format_timestamp(self, seconds: float) -> str:
        total_seconds = int(seconds)
        hours = total_seconds // 3600
        minutes = (total_seconds % 3600) // 60
        secs = total_seconds % 60

        if hours > 0:
            return f"{hours:02d}:{minutes:02d}:{secs:02d}"
        else:
            return f"{minutes:02d}:{secs:02d}"

    def validate_source(self, source: str | Path) -> None:
        super().validate_source(source)

        if isinstance(source, Path):
            raise ValueError("YouTube parser requires a URL, not a file path")

        if not self.can_handle(source):
            raise ValueError(f"Invalid YouTube URL: {source}")
