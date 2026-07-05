import doorboard_esp32_link
import doorboard_event_client


def test_package_imports() -> None:
    assert doorboard_esp32_link is not None
    assert doorboard_event_client is not None
