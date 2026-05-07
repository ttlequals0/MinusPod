"""Pin the public name + behavior of parse_vtt_to_segments after the 2.0.25 rename.

Was `_parse_vtt_to_segments` (module-private). Renamed for the offline LLM
benchmark, which imports it directly to feed transcripts through the same
parser production uses.
"""
from api.episodes import parse_vtt_to_segments


def test_parse_vtt_to_segments_renamed_export_callable():
    vtt = (
        "WEBVTT\n"
        "\n"
        "00:00:00.000 --> 00:00:05.000\n"
        "Welcome to the show.\n"
        "\n"
        "00:00:05.000 --> 00:00:10.000\n"
        "Today's guest is interesting.\n"
    )
    segments = parse_vtt_to_segments(vtt)
    assert len(segments) == 2
    assert segments[0]['start'] == 0.0
    assert segments[0]['end'] == 5.0
    assert 'Welcome' in segments[0]['text']
    assert segments[1]['start'] == 5.0
