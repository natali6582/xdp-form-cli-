from __future__ import annotations

from xdp_form_cli.field_naming import generate_field_name


def test_dictionary_label_maps_to_english_name() -> None:
    used: dict[str, int] = {}

    name = generate_field_name("שם פרטי", "text", used)

    assert name == "txtFirstName"


def test_english_label_is_cleaned_and_camelcased() -> None:
    used: dict[str, int] = {}

    name = generate_field_name("Full Name:", "text", used)

    assert name == "txtFullName"


def test_unknown_hebrew_label_is_transliterated() -> None:
    used: dict[str, int] = {}

    name = generate_field_name("שלום", "text", used)

    # ש=sh, ל=l, ו=v, ם=m
    assert name == "txtShlvm"


def test_checkbox_gets_chk_prefix() -> None:
    used: dict[str, int] = {}

    name = generate_field_name("Approved", "checkbox", used)

    assert name == "chkApproved"


def test_signature_gets_img_prefix_per_project_rule() -> None:
    # Project rule: signature placeholders are image fields named img...
    used: dict[str, int] = {}

    name = generate_field_name("חתימה", "signature", used)

    assert name == "imgSignature"


def test_date_gets_dt_prefix() -> None:
    used: dict[str, int] = {}

    name = generate_field_name("תאריך", "date", used)

    assert name == "dtDate"


def test_duplicate_names_get_numeric_suffix() -> None:
    used: dict[str, int] = {}

    first = generate_field_name("שם", "text", used)
    second = generate_field_name("שם", "text", used)
    third = generate_field_name("שם", "text", used)

    assert first == "txtName"
    assert second == "txtName_1"
    assert third == "txtName_2"


def test_empty_or_noise_label_falls_back_to_generic_name() -> None:
    used: dict[str, int] = {}

    first = generate_field_name("", "text", used)
    second = generate_field_name("12 34 :", "text", used)

    assert first == "txtField"
    assert second == "txtField_1"


def test_custom_dictionary_overrides_default() -> None:
    used: dict[str, int] = {}

    name = generate_field_name(
        "שם", "text", used, dictionary={"שם": "CustomerName"}
    )

    assert name == "txtCustomerName"
