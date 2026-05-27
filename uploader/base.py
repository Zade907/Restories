"""
Shared export abstractions for platform-specific uploaders.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class PlatformExportSpec:
    platform: str
    video_path: str
    title: str
    description: str
    hashtags: List[str] = field(default_factory=list)
    thumbnail_path: Optional[str] = None
    metadata: Dict[str, str] = field(default_factory=dict)


def build_export_spec(
    platform: str,
    video_path: str,
    title: str,
    description: str,
    hashtags: List[str],
    thumbnail_path: Optional[str] = None,
    **metadata,
) -> PlatformExportSpec:
    return PlatformExportSpec(
        platform=platform,
        video_path=video_path,
        title=title,
        description=description,
        hashtags=hashtags,
        thumbnail_path=thumbnail_path,
        metadata={key: str(value) for key, value in metadata.items()},
    )
