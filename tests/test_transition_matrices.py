import unittest

import pandas as pd

from parser_bot.transition_matrices import (
    _extract_first_matrix,
    _extract_year_values,
)


class TransitionMatrixTestCase(unittest.TestCase):
    def test_extract_first_matrix_with_probabilities(self) -> None:
        frame = pd.DataFrame(
            [
                ["", "осадки.", "0-10", "10-20", "20-30", "Сумма"],
                ["", "0-10", 3, 2, 1, 6],
                ["", "", 0.5, 0.3333333333, 0.1666666667, ""],
                ["", "10-20", 1, 4, 1, 6],
                ["", "", 0.1666666667, 0.6666666667, 0.1666666667, ""],
                ["", "20-30", 0, 1, 5, 6],
                ["", "", 0.0, 0.1666666667, 0.8333333333, ""],
            ]
        )

        class_labels, row_labels, count_matrix, probability_matrix = _extract_first_matrix(frame)

        self.assertEqual(class_labels, ("0-10", "10-20", "20-30"))
        self.assertEqual(row_labels, ("0-10", "10-20", "20-30"))
        self.assertEqual(count_matrix[0], (3.0, 2.0, 1.0))
        self.assertAlmostEqual(probability_matrix[1][1], 0.6666666667)

    def test_extract_year_values(self) -> None:
        frame = pd.DataFrame(
            [
                ["год", "апрель", "май"],
                [1966, 1.0, 2.0],
                [1967, 3.0, 4.0],
                [2012, 5.0, 6.0],
            ]
        )

        self.assertEqual(_extract_year_values(frame), [1966, 1967, 2012])


if __name__ == "__main__":
    unittest.main()
