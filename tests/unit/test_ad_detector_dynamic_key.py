
def test_ad_detector_dynamic_api_key_resolution(monkeypatch):
    """Verify that AdDetector.api_key dynamically resolves get_api_key() when not explicitly overridden."""
    from ad_detector import AdDetector
    import llm_client

    # 1. When constructed without an explicit key and get_api_key returns None
    monkeypatch.setattr(llm_client, "get_api_key", lambda: None)
    detector = AdDetector()
    assert detector.api_key is None

    # 2. When get_api_key later returns a key (e.g. after user updates settings in UI)
    monkeypatch.setattr(llm_client, "get_api_key", lambda: "sk-test-dynamic-key")
    assert detector.api_key == "sk-test-dynamic-key"

    # 3. Explicit override still takes precedence
    override_detector = AdDetector(api_key="sk-override-key")
    assert override_detector.api_key == "sk-override-key"
