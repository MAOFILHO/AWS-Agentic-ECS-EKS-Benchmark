"""LICENSE file and the README link to it (issue #169)."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LICENSE = ROOT / "LICENSE"


def test_license_is_standard_mit_with_copyright_line():
    text = LICENSE.read_text()
    assert text.startswith("MIT License")
    assert "Copyright (c) 2026 Marcos Oliveira" in text
    assert "Permission is hereby granted, free of charge" in text


def test_license_has_no_email_address():
    assert not re.search(r"\S+@\S+", LICENSE.read_text())


def test_readme_links_to_license():
    assert "[LICENSE](LICENSE)" in (ROOT / "README.md").read_text()
