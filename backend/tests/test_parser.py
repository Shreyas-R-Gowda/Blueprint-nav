from backend.app.services.parser import _fix_dimension_misread, _parse_dimension


def test_parse_dimension_token():
    assert _parse_dimension("30*40") == (30.0, 40.0)


def test_fix_dimension_token():
    assert _fix_dimension_misread("1O+40") == "10*40"
