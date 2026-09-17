"""packarr.nzbfile: filename-from-subject extraction and per-file size summation. Pure parsing, no network -
fetch() is exercised only for its input-validation edges (empty URL, non-NZB response)."""

from __future__ import annotations

from packarr.nzbfile import _filename, fetch, files


def _nzb(*file_els: str) -> bytes:
    return ('<?xml version="1.0"?><nzb xmlns="http://www.newzbin.com/DTD/2003/nzb">'
            + "".join(file_els) + "</nzb>").encode()


def _file(subject: str, *segment_bytes: int) -> str:
    segs = "".join(f'<segment bytes="{b}" number="{i+1}">x{i}@y</segment>' for i, b in enumerate(segment_bytes))
    return f'<file poster="a@b" date="1" subject="{subject}"><groups><group>g</group></groups><segments>{segs}</segments></file>'


def test_filename_extracted_from_quoted_subject():
    subj = '(Show FIXED) [03/34] - "Show.S01E07.1080p.mkv" yEnc (1/41)'
    assert _filename(subj, "fallback") == "Show.S01E07.1080p.mkv"


def test_filename_falls_back_to_stripped_subject_when_unquoted():
    assert _filename("Some.Show.S01E01.mkv (1/5)", "fallback") == "Some.Show.S01E01.mkv"


def test_filename_falls_back_to_index_when_subject_empty():
    assert _filename("", "file0") == "file0"


def test_files_extracts_name_and_sums_segment_bytes():
    nzb = _nzb(
        _file('(Pack) [00/02] - &quot;Show - 01.mkv&quot; yEnc (1/1)', 500_000, 500_000),
        _file('(Pack) [01/02] - &quot;Show - 02.mkv&quot; yEnc (1/1)', 300_000),
    )
    rows = files(nzb)
    assert rows == [(0, "Show - 01.mkv", 1_000_000), (1, "Show - 02.mkv", 300_000)]


def test_files_ignores_non_file_children():
    nzb = ('<?xml version="1.0"?><nzb xmlns="http://www.newzbin.com/DTD/2003/nzb">'
           '<head><meta type="title">x</meta></head>'
           + _file('&quot;a.mkv&quot;', 100) + "</nzb>").encode()
    rows = files(nzb)
    assert rows == [(0, "a.mkv", 100)]


def test_files_handles_missing_segments_gracefully():
    nzb = _nzb('<file poster="a" date="1" subject="&quot;a.mkv&quot;"><groups><group>g</group></groups></file>')
    assert files(nzb) == [(0, "a.mkv", 0)]


def test_fetch_empty_url_returns_none():
    assert fetch("") is None


def test_fetch_rejects_a_url_that_doesnt_actually_return_an_nzb(monkeypatch):
    import packarr.nzbfile as nzbfile_mod

    class FakeResponse:
        def read(self):
            return b"<html>404 not found</html>"

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(nzbfile_mod.urllib.request, "urlopen", lambda req, timeout=60: FakeResponse())
    assert fetch("https://indexer.example/get.nzb") is None
