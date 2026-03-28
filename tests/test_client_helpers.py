import unittest

from parser_bot.client import parse_partial_response, parse_select_options, sanitize_filename


class ClientHelpersTestCase(unittest.TestCase):
    def test_parse_select_options(self) -> None:
        html = '<select><option value=""></option><option value="TEMP">Температура воздуха</option></select>'
        self.assertEqual(
            parse_select_options(html),
            [("", ""), ("TEMP", "Температура воздуха")],
        )

    def test_parse_partial_response(self) -> None:
        xml = """
        <partial-response>
          <changes>
            <update id="form1:istd"><![CDATA[<select><option value="TEMP">T</option></select>]]></update>
            <update id="jakarta.faces.ViewState"><![CDATA[123:456]]></update>
          </changes>
        </partial-response>
        """
        updates = parse_partial_response(xml)
        self.assertEqual(updates["form1:istd"], '<select><option value="TEMP">T</option></select>')
        self.assertEqual(updates["jakarta.faces.ViewState"], "123:456")

    def test_sanitize_filename(self) -> None:
        self.assertEqual(sanitize_filename("Архив 2026/03/27.zip"), "Архив_2026_03_27.zip")


if __name__ == "__main__":
    unittest.main()
