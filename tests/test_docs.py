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
