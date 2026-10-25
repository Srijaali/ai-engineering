"""Keep the required flow-coverage check in the test suite."""
from scripts.check_flow import check_flow


def test_all_app_modules_are_documented():
    assert check_flow() == []
