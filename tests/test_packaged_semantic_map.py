from __future__ import annotations

import csv
from importlib.resources import files

from xdp_form_cli.field_name_resolution import (
    FieldNameResolver,
    load_known_field_names,
    load_semantic_field_aliases,
)


def _resource(name: str):
    return files("xdp_form_cli.resources").joinpath(name)


def _packaged_resolver() -> FieldNameResolver:
    with _resource("plan_t_fields.csv").open("r", encoding="utf-8-sig") as handle:
        known = {
            (row.get("field_name") or "").strip()
            for row in csv.DictReader(handle)
            if (row.get("field_name") or "").strip()
        }
    name_aliases, label_aliases = load_semantic_field_aliases(
        str(_resource("plan_t_semantic_labels.csv")), known
    )
    return FieldNameResolver(known, aliases=name_aliases, label_aliases=label_aliases)


def test_packaged_fields_list_covers_normalized_inventory() -> None:
    with _resource("plan_t_fields.csv").open("r", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))

    names = {row["field_name"] for row in rows}
    # The Plan-T inventory from pdf-fields-normalized.csv holds ~2000 fields.
    assert len(names) > 1500
    assert "txtAgentName" in names
    assert "imgPersonSignature" in names


def test_agent_name_description_resolves_to_plan_t_field() -> None:
    resolver = _packaged_resolver()

    resolution = resolver.resolve("txtShemSochen", field_type="text", label="שם סוכן")

    assert resolution.matched
    assert resolution.name == "txtAgentName"


def test_client_signature_description_resolves_to_image_field() -> None:
    resolver = _packaged_resolver()

    resolution = resolver.resolve(
        "imgChatima", field_type="image", label="חתימת לקוח - חתימה"
    )

    assert resolution.matched
    assert resolution.name == "imgPersonSignature"


def test_signature_date_description_resolves_to_text_field() -> None:
    resolver = _packaged_resolver()

    resolution = resolver.resolve(
        "dtTarich", field_type="text", label="תאריך חתימת לקוח"
    )

    assert resolution.matched
    assert resolution.name == "txtPersonSignatureDate"


def test_curated_labels_still_resolve_after_merge() -> None:
    resolver = _packaged_resolver()

    resolution = resolver.resolve("chkUSA", field_type="checkbox", label='אזרח ארה"ב')

    assert resolution.matched
    assert resolution.name == "chkCitizenUSA_yes"


def test_semantic_map_field_names_all_exist_in_fields_list() -> None:
    known = load_known_field_names(str(_resource("plan_t_fields.csv")))

    # Raises ValueError if any semantic row references an unknown field.
    name_aliases, label_aliases = load_semantic_field_aliases(
        str(_resource("plan_t_semantic_labels.csv")), known
    )

    assert len(label_aliases) > 1000
    assert name_aliases or label_aliases
