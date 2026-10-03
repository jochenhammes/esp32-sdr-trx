import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1]


def test_markdown_links_resolve():
    bad = []
    for md in list(ROOT.glob("*.md")) + list((ROOT).glob("docs/**/*.md")) + list(ROOT.glob("firmware/**/*.md")):
        text = md.read_text()
        for target in re.findall(r"\]\(([^)#\s]+)(?:#[^)]*)?\)", text):
            if target.startswith(("http://", "https://", "mailto:")):
                continue
            if not (md.parent / target).exists():
                bad.append(f"{md.relative_to(ROOT)} -> {target}")
    assert not bad, "\n".join(bad)


def test_the_two_copies_of_the_udev_rule_are_identical():
    assert (ROOT / "udev" / "70-espdr.rules").read_text() == (ROOT / "src" / "espdr" / "udev" / "70-espdr.rules").read_text()
