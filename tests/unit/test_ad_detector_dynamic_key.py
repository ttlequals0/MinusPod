
def test_ad_detector_dynamic_api_key_resolution(monkeypatch):
    """Verify that AdDetector.api_key dynamically resolves get_api_key() when not explicitly overridden."""
    import ad_detector
    from ad_detector import AdDetector

    # 1. When constructed without an explicit key and get_api_key returns None
    monkeypatch.setattr(ad_detector, "get_api_key", lambda: None)
    detector = AdDetector()
    assert detector.api_key is None

    # 2. When get_api_key later returns a key (e.g. after user updates settings in UI)
    monkeypatch.setattr(ad_detector, "get_api_key", lambda: "sk-test-dynamic-key")
    assert detector.api_key == "sk-test-dynamic-key"

    # 3. Explicit override still takes precedence
    override_detector = AdDetector(api_key="sk-override-key")
    assert override_detector.api_key == "sk-override-key"


def test_chapters_generator_dynamic_api_key_resolution(monkeypatch):
    """Verify that ChaptersGenerator.api_key dynamically resolves get_api_key() when not explicitly overridden."""
    import chapters_generator
    from chapters_generator import ChaptersGenerator

    # 1. When constructed without an explicit key and get_api_key returns None
    monkeypatch.setattr(chapters_generator, "get_api_key", lambda: None)
    generator = ChaptersGenerator()
    assert generator.api_key is None
    assert generator._chapter_prompt is None
    assert generator.chapters_degraded is False

    # 2. When get_api_key later returns a key
    monkeypatch.setattr(chapters_generator, "get_api_key", lambda: "sk-test-dynamic-key")
    assert generator.api_key == "sk-test-dynamic-key"

    # 3. Explicit override takes precedence
    override_generator = ChaptersGenerator(api_key="sk-override-key")
    assert override_generator.api_key == "sk-override-key"
