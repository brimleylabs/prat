from rich import filesize




def test_pick_unit_and_suffix():
    units = ["bytes", "KB", "MB", "GB", "TB", "PB", "EB", "ZB", "YB"]
    assert filesize.pick_unit_and_suffix(50, units, 1024) == (1, "bytes")
    assert filesize.pick_unit_and_suffix(2048, units, 1024) == (1024, "KB")
