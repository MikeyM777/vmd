"""The console page itself: it must at least parse.

A syntax error anywhere in that script disables the whole page - video, settings
and steering all stop, because the browser abandons the entire block. It cost a
live debugging session to learn that, so it is checked here.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

PAGE = Path(__file__).resolve().parents[1] / "vmd" / "webui" / "static" / "console.html"


def page_script() -> str:
    html = PAGE.read_text(encoding="utf-8")
    start = html.rindex("<script>") + len("<script>")
    return html[start : html.rindex("</script>")]


def test_the_page_exists_and_has_one_script_block() -> None:
    html = PAGE.read_text(encoding="utf-8")
    assert html.count("<script>") == 1
    assert html.count("</script>") == 1


def test_the_page_script_is_valid_javascript(tmp_path: Path) -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed; cannot parse the page script")
    script = tmp_path / "console.js"
    script.write_text(page_script(), encoding="utf-8")
    result = subprocess.run(
        [node, "--check", str(script)], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, f"the console page does not parse:\n{result.stderr}"


def test_the_page_invents_no_measurements() -> None:
    """Every number on screen comes from the server or reads as unknown.

    These are the literals that were once typed into the markup and looked like
    readings from a camera that was not connected.
    """
    html = PAGE.read_text(encoding="utf-8")
    for invented in ("−63 dBm", "42 Mb/s", "512 / 600 GB", "11 days", "person@700m", "MOCKUP"):
        assert invented not in html, f"{invented!r} is a made-up reading and must not be on screen"


def test_no_simulation_controls_remain() -> None:
    html = PAGE.read_text(encoding="utf-8")
    assert "simulate movement" not in html
    assert "simulate link loss" not in html
