"""The published assignment's structural key, as one string (usa-wa#370)."""

import pytest

from usa_wa_pipeline.conformed.span_key import SPAN_KEY_SEPARATOR, span_key


def test_the_five_fields_join_in_the_published_order() -> None:
    """The order is the dataset's own column order, so a reader can line the key
    up against the row it came from without consulting a spec."""
    assert span_key(
        entity_id="01KWWWM9CT3EKCSJZZ0CNZ3NJT",
        role_key="committee-member-role:31635",
        span_kind="committee",
        span_discriminator="31635",
        span_start_biennium="2021-22",
    ) == ("01KWWWM9CT3EKCSJZZ0CNZ3NJT|committee-member-role:31635|committee|31635|2021-22")


def test_a_field_carrying_the_separator_is_refused() -> None:
    """The guarantee power-map#490 asked for is that five fields become one string
    the same way on both sides. A value containing the separator would make the
    string ambiguous — two different tuples could serialize identically, and PM
    would re-key a crosswalk row onto the wrong assignment. `:` already appears in
    `role_key`, which is why the separator is not `:`; refusing is what keeps the
    next vocabulary addition from silently reintroducing the problem."""
    with pytest.raises(ValueError, match="separator"):
        span_key(
            entity_id="01KWWWM9CT3EKCSJZZ0CNZ3NJT",
            role_key=f"committee{SPAN_KEY_SEPARATOR}member",
            span_kind="committee",
            span_discriminator="31635",
            span_start_biennium="2021-22",
        )


@pytest.mark.parametrize("blank", ["", "   "])
def test_an_empty_field_is_refused(blank: str) -> None:
    """An absent field collapses two adjacent separators, which makes the key
    unparseable and — worse — makes two rows differing only in that field collide."""
    with pytest.raises(ValueError, match="empty"):
        span_key(
            entity_id="01KWWWM9CT3EKCSJZZ0CNZ3NJT",
            role_key=blank,
            span_kind="committee",
            span_discriminator="31635",
            span_start_biennium="2021-22",
        )


def test_the_separator_is_absent_from_every_published_value_today() -> None:
    """A regression rail on the choice itself: `|` was picked because no value in
    any of the five fields contains it (the live charset is `-0-9:A-Za-z`). If a
    future discriminator or role key needs it, the refusal above fires and this
    states why the separator would have to move."""
    assert SPAN_KEY_SEPARATOR not in "-0123456789:ABCDEFGHJKMNPQRSTVWXYZabcdefghilmnoprstuvy"
