"""Which audio a voice search accepts (Story 6.3): decided from the bytes, never from the
declared content type or file name.

Browsers record WebM/Opus (Chrome, Edge, Firefox, newer Safari), MP4 (Safari) or Ogg/Opus; WAV is
accepted for tools. The type returned is the one the provider documents: MP4 audio goes as
`audio/m4a`, since `audio/mp4` is not in Gemini's list.
"""


def content_type(data: bytes) -> str | None:
    """The provider content type of a recording, or None when the container is not accepted."""
    if data[:4] == b"\x1a\x45\xdf\xa3" and b"webm" in data[:64]:  # EBML with the webm doctype
        return "audio/webm"
    if data[:4] == b"OggS":
        return "audio/ogg"
    if data[4:8] == b"ftyp":
        return "audio/m4a"
    if data[:4] == b"RIFF" and data[8:12] == b"WAVE":
        return "audio/wav"
    return None
