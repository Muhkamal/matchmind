from pathlib import Path

import pytest

from src import teams

FIX = Path(__file__).parent / "fixtures" / "e0_team_names.txt"


def test_every_csv_name_maps_and_slugs_unique():
    names = [n.strip() for n in FIX.read_text().splitlines() if n.strip()]
    assert len(names) == 43
    slugs = {teams.canon(n, "csv") for n in names}
    assert len(slugs) == 43 and slugs <= set(teams.TEAMS)


@pytest.mark.parametrize("raw,slug", [("Manchester City FC", "man-city"), ("man city", "man-city"),
                                      ("AFC Bournemouth", "bournemouth"), ("Spurs", None)])
def test_aliases(raw, slug):
    if slug is None:
        with pytest.raises(teams.UnknownTeamError):
            teams.canon(raw)
    else:
        assert teams.canon(raw, "fdo") == slug


def test_unknown_is_hard_error_not_average_team():
    with pytest.raises(teams.UnknownTeamError):
        teams.canon("Atlantis United", "csv")
    with pytest.raises(teams.UnknownTeamError):
        teams.canon("", "csv")


def test_split_fixture_multiword_both_sides():
    assert teams.split_fixture("Man City Aston Villa".split()) == ("man-city", "aston-villa")
    assert teams.split_fixture("Aston Villa Man United".split()) == ("aston-villa", "man-united")
    with pytest.raises(teams.UnknownTeamError):
        teams.split_fixture(["Arsenal"])


def test_display_names():
    assert teams.display("man-city") == "Man City"
    assert teams.pretty_fixture("arsenal", "chelsea") == "Arsenal – Chelsea"
