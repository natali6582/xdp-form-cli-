from __future__ import annotations

import json
from pathlib import Path

from xdp_form_cli.detection_patterns import classify_label, load_patterns


def test_default_patterns_classify_date_labels() -> None:
    patterns = load_patterns(None)

    field_type, width = classify_label("Date of birth", patterns)

    assert field_type == "date"
    assert width > 0


def test_default_patterns_classify_hebrew_signature() -> None:
    patterns = load_patterns(None)

    field_type, _ = classify_label("חתימת המשקיע", patterns)

    assert field_type == "signature"


def test_default_patterns_classify_hebrew_date() -> None:
    patterns = load_patterns(None)

    field_type, _ = classify_label("תאריך לידה", patterns)

    assert field_type == "date"


def test_address_label_gets_wider_field() -> None:
    patterns = load_patterns(None)

    _, address_width = classify_label("כתובת", patterns)
    _, plain_width = classify_label("הערה כללית", patterns)

    assert address_width > plain_width


def test_unmatched_label_defaults_to_text() -> None:
    patterns = load_patterns(None)

    field_type, width = classify_label("Random words here", patterns)

    assert field_type == "text"
    assert width > 0


def test_user_patterns_file_extends_defaults(tmp_path: Path) -> None:
    custom = tmp_path / "detection-patterns.json"
    custom.write_text(
        json.dumps(
            {"patterns": [{"match": "מספר עוסק", "type": "text", "width": 99}]}
        ),
        encoding="utf-8",
    )

    patterns = load_patterns(custom)

    field_type, width = classify_label("מספר עוסק מורשה", patterns)
    assert field_type == "text"
    assert width == 99

    # Defaults still apply after merging.
    assert classify_label("Signature", patterns)[0] == "signature"


def test_checkbox_glyph_labels_are_classified_as_checkbox() -> None:
    patterns = load_patterns(None)

    field_type, _ = classify_label("☐ I agree to the terms", patterns)

    assert field_type == "checkbox"
