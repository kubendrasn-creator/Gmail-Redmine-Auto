"""
Attachment handling pipeline for Gmail-Redmine integration.

Handles file size validation, video compression, and upload planning.
"""
import shutil
import subprocess
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import List, Optional

from backend.config.settings import (
    EMAIL_ATTACHMENTS_DIR,
    REDMINE_MAX_ATTACHMENT_MB,
    VIDEO_COMPRESSION_ENABLED,
)
from backend.utils.logger import get_logger

logger = get_logger("attachments")

# Video extensions that can be compressed
VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm", ".avi"}

# Minimum video bitrate threshold (below this, compression would produce unwatchable video)
MIN_VIDEO_BITRATE = 150_000  # 150 kbps


class AttachmentAction(Enum):
    """Action to take for an attachment."""
    UPLOAD = "UPLOAD"
    COMPRESS = "COMPRESS"
    SKIP = "SKIP"


@dataclass
class AttachmentPlan:
    """
    Plan for handling an attachment (dry-run/preview).
    Used for confirmation prompts and dry-run mode.
    """
    name: str
    original_size: int  # bytes
    final_size: int  # bytes (estimated for compression)
    action: AttachmentAction
    note: Optional[str] = None
    local_path: Optional[Path] = None


@dataclass
class AttachmentResult:
    """
    Result of executing an attachment plan.
    """
    name: str
    original_size: int
    final_size: int
    action: AttachmentAction
    token: Optional[str] = None
    note: Optional[str] = None
    local_path: Optional[Path] = None


class AttachmentPipeline:
    """
    Handles attachment processing: validation, compression, and upload planning.
    """

    def __init__(self, max_size_mb: int = None):
        self.max_size_bytes = (max_size_mb or REDMINE_MAX_ATTACHMENT_MB) * 1024 * 1024
        self.compression_enabled = VIDEO_COMPRESSION_ENABLED

    def plan_attachments(self, paths: List[Path]) -> List[AttachmentPlan]:
        """
        Create attachment plans without side effects.
        Used for confirmation prompts and dry-run mode.
        """
        plans = []

        for path in paths:
            if not path.exists():
                plans.append(AttachmentPlan(
                    name=path.name,
                    original_size=0,
                    final_size=0,
                    action=AttachmentAction.SKIP,
                    note="File not found",
                    local_path=path,
                ))
                continue

            size = path.stat().st_size
            ext = path.suffix.lower()

            if size <= self.max_size_bytes:
                # File is within limit - upload as-is
                plans.append(AttachmentPlan(
                    name=path.name,
                    original_size=size,
                    final_size=size,
                    action=AttachmentAction.UPLOAD,
                    local_path=path,
                ))
            elif ext in VIDEO_EXTENSIONS and self.compression_enabled:
                # Video over limit - plan compression
                compressed_size = self._estimate_compressed_size(path, size)
                if compressed_size and compressed_size <= self.max_size_bytes:
                    plans.append(AttachmentPlan(
                        name=path.name,
                        original_size=size,
                        final_size=compressed_size,
                        action=AttachmentAction.COMPRESS,
                        note=f"Will compress from {size / 1024 / 1024:.1f} MB to ~{compressed_size / 1024 / 1024:.1f} MB",
                        local_path=path,
                    ))
                else:
                    # Compression would not help or is not possible
                    plans.append(AttachmentPlan(
                        name=path.name,
                        original_size=size,
                        final_size=size,
                        action=AttachmentAction.SKIP,
                        note=f"Size {size / 1024 / 1024:.1f} MB exceeds limit ({self.max_size_bytes / 1024 / 1024:.1f} MB)",
                        local_path=path,
                    ))
            else:
                # Non-video or compression disabled - skip
                plans.append(AttachmentPlan(
                    name=path.name,
                    original_size=size,
                    final_size=size,
                    action=AttachmentAction.SKIP,
                    note=f"Size {size / 1024 / 1024:.1f} MB exceeds limit ({self.max_size_bytes / 1024 / 1024:.1f} MB)",
                    local_path=path,
                ))

        return plans

    def execute_attachments(self, redmine_client, plans: List[AttachmentPlan]) -> List[AttachmentResult]:
        """
        Execute attachment plans: compress if needed, upload to Redmine.
        """
        results = []

        for plan in plans:
            try:
                if plan.action == AttachmentAction.SKIP:
                    results.append(AttachmentResult(
                        name=plan.name,
                        original_size=plan.original_size,
                        final_size=plan.final_size,
                        action=AttachmentAction.SKIP,
                        note=plan.note,
                        local_path=plan.local_path,
                    ))
                    continue

                file_path = plan.local_path
                if not file_path or not file_path.exists():
                    results.append(AttachmentResult(
                        name=plan.name,
                        original_size=plan.original_size,
                        final_size=plan.final_size,
                        action=AttachmentAction.SKIP,
                        note="File not found",
                        local_path=file_path,
                    ))
                    continue

                if plan.action == AttachmentAction.COMPRESS:
                    compressed_path = self._compress_video(file_path)
                    if compressed_path and compressed_path.exists():
                        file_path = compressed_path
                        logger.info(f"Compressed {plan.name} to {compressed_path.stat().st_size / 1024 / 1024:.1f} MB")
                    else:
                        results.append(AttachmentResult(
                            name=plan.name,
                            original_size=plan.original_size,
                            final_size=plan.final_size,
                            action=AttachmentAction.SKIP,
                            note="Compression failed",
                            local_path=plan.local_path,
                        ))
                        continue

                # Upload to Redmine
                token = redmine_client.upload_attachment(str(file_path))
                final_size = file_path.stat().st_size

                results.append(AttachmentResult(
                    name=plan.name,
                    original_size=plan.original_size,
                    final_size=final_size,
                    action=plan.action,
                    token=token,
                    local_path=plan.local_path,
                ))

                # Clean up compressed file if it exists
                if plan.action == AttachmentAction.COMPRESS and file_path != plan.local_path:
                    file_path.unlink(missing_ok=True)

            except Exception as e:
                logger.error(f"Failed to process attachment {plan.name}: {e}", exc_info=True)
                results.append(AttachmentResult(
                    name=plan.name,
                    original_size=plan.original_size,
                    final_size=plan.original_size,
                    action=AttachmentAction.SKIP,
                    note=f"Upload failed: {str(e)}",
                    local_path=plan.local_path,
                ))

        return results

    def _estimate_compressed_size(self, path: Path, original_size: int) -> Optional[int]:
        """
        Estimate compressed video size using bitrate calculation.
        Returns estimated size in bytes, or None if compression would not help.
        """
        try:
            duration = self._get_video_duration(path)
            if not duration:
                return None

            # Calculate target video bitrate
            # Formula: (max_bytes * 8 * 0.90 / duration) - 64000 (audio)
            target_bitrate = (self.max_size_bytes * 8 * 0.90 / duration) - 64_000

            if target_bitrate < MIN_VIDEO_BITRATE:
                # Compression would produce unwatchable video
                return None

            # Estimate final size
            # (target_bitrate + 64000) * duration / 8
            estimated_size = int((target_bitrate + 64_000) * duration / 8)

            return estimated_size

        except Exception as e:
            logger.warning(f"Could not estimate compressed size for {path}: {e}")
            return None

    def _compress_video(self, path: Path) -> Optional[Path]:
        """
        Compress a video file using ffmpeg.
        Returns path to compressed file, or None on failure.
        """
        if not self.compression_enabled:
            return None

        ffprobe_path = shutil.which("ffprobe")
        ffmpeg_path = shutil.which("ffmpeg")

        if not ffprobe_path or not ffmpeg_path:
            logger.warning("FFmpeg/FFprobe not available, skipping compression")
            return None

        try:
            duration = self._get_video_duration(path)
            if not duration:
                return None

            # Calculate target bitrate
            target_bitrate = (self.max_size_bytes * 8 * 0.90 / duration) - 64_000

            if target_bitrate < MIN_VIDEO_BITRATE:
                logger.warning(f"Target bitrate {target_bitrate} too low, skipping compression")
                return None

            output_path = path.parent / f"{path.stem}.compressed.mp4"

            # Build ffmpeg command
            cmd = [
                ffmpeg_path,
                "-i", str(path),
                "-vf", "scale='min(1280,iw)':-2,fps=15",
                "-b:v", str(int(target_bitrate)),
                "-maxrate", str(int(target_bitrate * 1.2)),
                "-bufsize", str(int(target_bitrate * 2)),
                "-c:a", "aac",
                "-b:a", "64k",
                "-movflags", "+faststart",
                "-y",  # Overwrite output file
                str(output_path),
            ]

            logger.info(f"Compressing {path.name} with ffmpeg...")
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=900,  # 15 minutes max
            )

            if result.returncode != 0:
                logger.error(f"FFmpeg failed: {result.stderr}")
                return None

            if not output_path.exists():
                logger.error("Compressed file not created")
                return None

            compressed_size = output_path.stat().st_size
            logger.info(f"Compressed to {compressed_size / 1024 / 1024:.1f} MB")

            return output_path

        except subprocess.TimeoutExpired:
            logger.error(f"FFmpeg compression timed out for {path}")
            return None
        except Exception as e:
            logger.error(f"Failed to compress {path}: {e}", exc_info=True)
            return None

    def _get_video_duration(self, path: Path) -> Optional[float]:
        """
        Get video duration in seconds using ffprobe.
        """
        ffprobe_path = shutil.which("ffprobe")
        if not ffprobe_path:
            return None

        try:
            cmd = [
                ffprobe_path,
                "-v", "error",
                "-show_entries", "format=duration",
                "-of", "default=noprint_wrappers=1:nokey=1",
                str(path),
            ]

            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=30,
            )

            if result.returncode != 0:
                logger.warning(f"FFprobe failed: {result.stderr}")
                return None

            duration = float(result.stdout.strip())
            return duration

        except Exception as e:
            logger.warning(f"Could not get video duration: {e}")
            return None
