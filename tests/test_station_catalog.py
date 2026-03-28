import unittest

from parser_bot.station_catalog import coordinate_to_decimal, parse_station_catalog_text


class StationCatalogTestCase(unittest.TestCase):
    def test_coordinate_to_decimal_positive(self) -> None:
        self.assertEqual(coordinate_to_decimal("55°50′"), 55.833333)

    def test_coordinate_to_decimal_negative(self) -> None:
        self.assertEqual(coordinate_to_decimal("-179°38′"), -179.633333)

    def test_coordinate_to_decimal_broken_excel_coordinate(self) -> None:
        self.assertEqual(coordinate_to_decimal(" о07’  \n65"), 65.116667)

    def test_parse_station_catalog_text(self) -> None:
        text = """
  1   20046    Им.Э.Т.Кренкеля         80°37′    58°03′       21       1957
  2   20087    Им.Г.А.Ушакова          79°33′    90°37′        7       1930    До 2015г. наз. Остров Голомянный
               (Голомянный)
147   25173    Мыс Шмидта              68°54′    -179°38′      2       1932    Перенос в 1953 г. на 7км к В.
148   25174    Тестовая                68°55′    179°38′       3       1934    1965г.- ст. сгорела; 1969г.- открыта в 7км к СВ
149   25175    Закрытая                68°56′    178°38′       4       1935    Закрыта 18.04.2016г.
"""
        entries = parse_station_catalog_text(text)

        self.assertEqual(len(entries), 5)
        self.assertEqual(entries[1].station_name, "Им.Г.А.Ушакова (Голомянный)")
        self.assertEqual(entries[1].note, "До 2015г. наз. Остров Голомянный")
        self.assertEqual(entries[1].rename_note, "До 2015г. наз. Остров Голомянный")
        self.assertEqual(entries[2].longitude, -179.633333)
        self.assertEqual(entries[2].transfer_note, "Перенос в 1953 г. на 7км к В.")
        self.assertEqual(entries[3].incident_note, "1965г.- ст. сгорела")
        self.assertTrue(entries[3].is_likely_active)
        self.assertEqual(entries[3].status_label, "После инцидента восстановлена")
        self.assertFalse(entries[4].is_likely_active)
        self.assertEqual(entries[4].end_year, 2016)
        self.assertEqual(entries[4].status_label, "Закрыта")


if __name__ == "__main__":
    unittest.main()
