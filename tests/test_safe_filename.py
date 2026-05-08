from legal_agent.services.draft_service import _safe_filename


def test_plain_title_gets_md_extension():
    assert _safe_filename("My Draft") == "My Draft.md"


def test_strips_windows_illegal_chars():
    assert _safe_filename('Bail App: <Test> "v1"/draft?') == "Bail App Test v1draft.md"


def test_strips_control_chars():
    assert _safe_filename("Title\x00with\x1fcontrol") == "Titlewithcontrol.md"


def test_strips_path_separators():
    assert _safe_filename("a/b\\c|d") == "abcd.md"


def test_trims_leading_and_trailing_whitespace():
    assert _safe_filename("   spaced   ") == "spaced.md"


def test_strips_trailing_dots_windows_reserved():
    assert _safe_filename("My Doc.") == "My Doc.md"
    assert _safe_filename("My Doc...") == "My Doc.md"
    assert _safe_filename("My Doc. . .") == "My Doc.md"


def test_does_not_double_append_md_extension():
    assert _safe_filename("Final Draft.md") == "Final Draft.md"
    assert _safe_filename("Final Draft.MD") == "Final Draft.md"


def test_empty_title_falls_back():
    assert _safe_filename("") == "Draft.md"
    assert _safe_filename(None) == "Draft.md"
    assert _safe_filename("   ") == "Draft.md"


def test_title_made_empty_by_sanitization_falls_back():
    assert _safe_filename('<<<>>>') == "Draft.md"
    assert _safe_filename("///") == "Draft.md"


def test_long_title_is_truncated():
    long_title = "A" * 500
    out = _safe_filename(long_title)
    assert out.endswith(".md")
    assert len(out) <= 200 + len(".md")


def test_custom_fallback_used_when_empty():
    assert _safe_filename("", fallback="Untitled") == "Untitled.md"


def test_preserves_unicode_in_title():
    assert _safe_filename("मराठी मसौदा") == "मराठी मसौदा.md"
