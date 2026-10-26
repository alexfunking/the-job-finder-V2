from __future__ import annotations

from jobfinder.text import html_to_text


def test_paragraphs_and_line_breaks() -> None:
    assert html_to_text("<p>First.</p><p>Second<br>line.</p>") == "First.\n\nSecond\nline."


def test_lists_get_dashes() -> None:
    html = "<p>Requirements:</p><ul><li>3+ years of Python</li><li>SQL</li></ul>"
    assert html_to_text(html) == "Requirements:\n\n- 3+ years of Python\n- SQL"


def test_entities_and_nbsp_are_decoded() -> None:
    assert html_to_text("<p>R&amp;D&nbsp;team &lt;Tel Aviv&gt;</p>") == "R&D team <Tel Aviv>"


def test_inline_tags_do_not_break_lines() -> None:
    assert html_to_text("<p>Work <strong>hybrid</strong>, <a href='x'>3 days</a> on site</p>") == (
        "Work hybrid, 3 days on site"
    )


def test_script_and_style_are_dropped() -> None:
    assert html_to_text("<style>p{}</style><p>Visible</p><script>track()</script>") == "Visible"


def test_whitespace_is_collapsed_and_blank_lines_limited() -> None:
    html = "<div>  a   lot\tof   space </div>\n\n\n<div></div><div></div><p>end</p>"
    assert html_to_text(html) == "a lot of space\n\nend"


def test_hebrew_text_survives() -> None:
    assert html_to_text("<p>משרת סטודנט, 3 ימים במשרד</p>") == "משרת סטודנט, 3 ימים במשרד"


def test_empty_and_tag_only_input() -> None:
    assert html_to_text("") == ""
    assert html_to_text("<p> </p><br>") == ""
