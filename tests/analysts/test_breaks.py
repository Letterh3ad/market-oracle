from oracle.analysts.breaks import first_break, is_price, steady


def test_a_move_outside_half_to_double_or_a_zero_is_a_break():
    assert first_break([100.0, 120.0, 90.0]) is None
    assert first_break([100.0, 49.0, 50.0]) == 1
    assert first_break([100.0, 201.0]) == 1
    assert first_break([0.0, 100.0]) == 0
    assert first_break([100.0, None]) == 1
    assert steady([100.0, 200.0, 100.0]) and not steady([100.0, 250.0])
    assert is_price(0.01) and not is_price(0.0) and not is_price(None)
