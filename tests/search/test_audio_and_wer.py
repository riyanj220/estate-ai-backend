import pytest
from django.core.management import call_command

from apps.kernel import runtime
from apps.kernel.ai.stub import stub
from apps.search import audio, wer


@pytest.mark.parametrize(
    ("data", "kind"),
    [
        (b"\x1a\x45\xdf\xa3\x9f\x42\x82\x84webm", "audio/webm"),
        (b"OggS\x00\x02", "audio/ogg"),
        (b"\x00\x00\x00\x20ftypiso5", "audio/m4a"),  # MP4 goes as the documented type
        (b"RIFF\x00\x00\x00\x00WAVE", "audio/wav"),
        (b"\x1a\x45\xdf\xa3\x9f\x42\x82\x88matroska", None),
        (b"RIFF\x00\x00\x00\x00AVI ", None),
        (b"ID3\x04", None),
        (b"<html>", None),
        (b"", None),
    ],
)
def test_the_type_comes_from_the_bytes(data, kind):
    assert audio.content_type(data) == kind


@pytest.mark.parametrize(
    ("heard", "wrong"),
    [
        ("A house in Clifton.", 0),
        ("a house in clifton under", 1),  # insertion
        ("a house clifton", 1),  # deletion
        ("a flat in clifton", 1),  # substitution
        ("", 4),
    ],
)
def test_word_errors(heard, wrong):
    assert wer.errors("a house in Clifton", heard) == (wrong, 4)


@pytest.mark.django_db
def test_the_speech_evaluation_reports_word_errors_by_group(tmp_path, capsys):
    (tmp_path / "en-one.wav").write_bytes(b"RIFF\x00\x00\x00\x00WAVE")
    (tmp_path / "en-one.txt").write_text("a house in Clifton", "utf-8")
    stub.reset()
    stub.reply = "a flat in Clifton"
    try:
        call_command("evaluate_speech", str(tmp_path), pause=0)
    finally:
        runtime.shutdown()
        stub.reset()
    assert "en: word error rate 25.0% over 1 clips" in capsys.readouterr().out
