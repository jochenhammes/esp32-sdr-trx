#!/usr/bin/env python3
"""Writes docs/espdr-rx.md and docs/espdr-tx.md from the commands' own --help, so that the reference cannot drift from the code.

  python scripts/gen-reference.py          # rewrite the two files
  python scripts/gen-reference.py --check  # exit 1 if they differ from this Python's output (the wording of argparse varies a little
                                           # between Python versions, so CI only checks that every option is listed: tests/test_cli.py)
"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))


def render(name, build, intro):
    os.environ["COLUMNS"] = "100"
    parser = build()
    help_text = parser.format_help().rstrip()
    return f"# {name}\n\n{intro}\n\nThis page is generated from `{name} -h` (`scripts/gen-reference.py`); the text below is exactly what the command prints.\n\n```text\n{help_text}\n```\n"


def documents():
    from espdr import cli_rx, cli_tx
    return {
        "docs/espdr-rx.md": render("espdr-rx", cli_rx.build_parser,
                                   "Receiver: loads the receiver firmware if needed and runs the rtl_tcp server. Guide: [receiver.md](receiver.md)."),
        "docs/espdr-tx.md": render("espdr-tx", cli_tx.build_parser,
                                   "Transmitter: FM and SSB voice on 13 cm from a WAV file, a pipe or a sound card, and RTTY from a text. **Read the "
                                   "[transmitter guide](transmitter.md) first (licence, safety, tuning).**"),
    }


def main():
    docs = documents()
    if "--check" in sys.argv:
        bad = [p for p, text in docs.items() if not (ROOT / p).exists() or (ROOT / p).read_text() != text]
        if bad:
            print("out of date:", ", ".join(bad), "- run python scripts/gen-reference.py")
            return 1
        return 0
    for p, text in docs.items():
        (ROOT / p).write_text(text)
        print("wrote", p)
    return 0


if __name__ == "__main__":
    sys.exit(main())
