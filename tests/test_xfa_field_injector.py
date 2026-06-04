from __future__ import annotations

from xdp_form_cli.auto_form import AutoFieldSpec
from xdp_form_cli.xfa_field_injector import XFA_TEMPLATE_NS, _build_field_element


def _tag(name: str) -> str:
    return f"{{{XFA_TEMPLATE_NS}}}{name}"


def test_image_fields_are_xfa_push_buttons_with_raised_grey_appearance() -> None:
    field = _build_field_element(
        AutoFieldSpec(
            page=1,
            name="imgPersonSignature",
            field_type="image",
            x=155,
            y=190,
            w=81,
            h=17,
        ),
        page_height_pt=792,
    )

    ui = field.find(_tag("ui"))
    assert ui is not None
    button = ui.find(_tag("button"))
    assert button is not None
    assert button.get("highlight") == "push"
    assert ui.find(_tag("imageEdit")) is None

    font = field.find(_tag("font"))
    assert font is not None
    assert font.get("typeface") == "Myriad Pro"

    caption_para = field.find(f"{_tag('caption')}/{_tag('para')}")
    assert caption_para is not None
    assert caption_para.get("vAlign") == "middle"
    assert caption_para.get("hAlign") == "center"

    border = field.find(_tag("border"))
    assert border is not None
    assert border.get("hand") == "right"
    assert border.get("break") == "open"
    edge = border.find(_tag("edge"))
    assert edge is not None
    assert edge.get("stroke") == "raised"
    assert edge.get("cap") == "butt"
    color = border.find(f"{_tag('fill')}/{_tag('color')}")
    assert color is not None
    assert color.get("value") == "212,208,200"

    bind = field.find(_tag("bind"))
    assert bind is not None
    assert bind.get("match") == "none"
    assert field.find(_tag("value")) is None
