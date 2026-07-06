from pydantic import BaseModel, Field


class KindRetentionPolicy(BaseModel):
    max_age_s: int = Field(
        description="Maximum age of recordings of this kind in seconds before deletion"
    )
    max_size_bytes: int = Field(
        description="Maximum total storage size allowed for this kind of recording in bytes"
    )


class RetentionConfig(BaseModel):
    # Global caps
    min_free_bytes: int = Field(
        default=4 * 1024**3,  # 4 GiB
        description="Stop recording when free SSD space falls below this threshold in bytes",
    )
    max_recording_bytes: int = Field(
        default=48 * 1024**3,  # 48 GiB
        description="Global maximum storage allowed for all recordings in bytes",
    )

    # Per-kind policies
    bell_clip: KindRetentionPolicy = Field(
        default_factory=lambda: KindRetentionPolicy(
            max_age_s=3 * 24 * 3600,  # 3 days
            max_size_bytes=10 * 1024**3,  # 10 GiB
        )
    )
    video_message: KindRetentionPolicy = Field(
        default_factory=lambda: KindRetentionPolicy(
            max_age_s=14 * 24 * 3600,  # 14 days
            max_size_bytes=30 * 1024**3,  # 30 GiB
        )
    )
    photo_booth: KindRetentionPolicy = Field(
        default_factory=lambda: KindRetentionPolicy(
            max_age_s=7 * 24 * 3600,  # 7 days
            max_size_bytes=8 * 1024**3,  # 8 GiB
        )
    )
