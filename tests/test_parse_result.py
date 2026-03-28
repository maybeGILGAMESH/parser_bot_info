import tempfile
import unittest
import zipfile
from pathlib import Path

from parser_bot.parse_result import parse_result_zip


class ParseResultZipTestCase(unittest.TestCase):
    def test_parse_result_zip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            zip_path = Path(tmp_dir) / "sample.zip"
            with zipfile.ZipFile(zip_path, "w") as archive:
                archive.writestr(
                    "fld1.txt",
                    " 1  1       5  Индекс ВМО\r\n 2  2       4  Год\r\n 3  3     5,1  Январь\r\n".encode("cp1251"),
                )
                archive.writestr("statlist1.txt", "27612 Москва, ВДНХ\r\n".encode("cp1251"))
                archive.writestr("wr1.txt", "27612 2022 -5.4\r\n".encode("cp1251"))

            parsed = parse_result_zip(zip_path)

        self.assertEqual(parsed.field_names, ["Индекс ВМО", "Год", "Январь"])
        self.assertEqual(parsed.stations, {"27612": "Москва, ВДНХ"})
        self.assertEqual(
            parsed.records,
            [
                {
                    "Индекс ВМО": "27612",
                    "Год": 2022,
                    "Январь": -5.4,
                    "Название станции": "Москва, ВДНХ",
                }
            ],
        )


if __name__ == "__main__":
    unittest.main()
