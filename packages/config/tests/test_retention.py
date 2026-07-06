from doorboard_config import RetentionConfig


def test_retention_config_defaults():
    config = RetentionConfig()
    assert config.min_free_bytes == 4 * 1024**3
    assert config.max_recording_bytes == 48 * 1024**3

    assert config.bell_clip.max_age_s == 3 * 24 * 3600
    assert config.bell_clip.max_size_bytes == 10 * 1024**3

    assert config.video_message.max_age_s == 14 * 24 * 3600
    assert config.video_message.max_size_bytes == 30 * 1024**3

    assert config.photo_booth.max_age_s == 7 * 24 * 3600
    assert config.photo_booth.max_size_bytes == 8 * 1024**3
