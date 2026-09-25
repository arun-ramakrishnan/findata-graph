from helpers.misc import license_check


def test_current_tree_has_license_metadata_and_inventory():
    assert license_check.check() == []
