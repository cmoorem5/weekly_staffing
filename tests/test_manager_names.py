"""Tests for canonical manager name normalization."""

from staffing_tool.manager_names import canonical_manager_name

ROSTER = frozenset(
    {
        "AHLSTEDT",
        "DENISON",
        "DOHERTY",
        "ENDER",
        "ESTANISLAO",
        "HOLST",
        "KADOW",
        "MOORE",
        "POWERS",
        "BOWMAN",
        "FARKAS",
        "MUSZALSKI",
        "STECKEVICZ",
        "WALLACE",
    }
)


def test_canonical_from_row_type_prefix():
    assert canonical_manager_name("m, Ender", ROSTER) == "Ender"
    assert canonical_manager_name("P, Doherty", ROSTER) == "Doherty"
    assert canonical_manager_name("P, Wallace", ROSTER) == "Wallace"


def test_canonical_from_composite_row_label():
    assert (
        canonical_manager_name("1-Jonathan Tonelli, Holst", ROSTER) == "Holst"
    )


def test_canonical_already_normalized():
    assert canonical_manager_name("Estanislao", ROSTER) == "Estanislao"


def test_canonical_unknown_passthrough():
    assert canonical_manager_name("Smith, John", ROSTER) == "Smith, John"


def test_canonical_empty():
    assert canonical_manager_name("", ROSTER) == "(unknown)"
