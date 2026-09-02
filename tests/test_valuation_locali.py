from valuation import coeff_locali


def test_coeff_locali_accepts_numeric_three_equivalent_to_existing_three_room_inputs():
    assert coeff_locali(3) == coeff_locali("3") == coeff_locali("Trilocale") == 1.00
