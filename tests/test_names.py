"""Names of cleaned copies: no date or time from the original's name."""
import unittest

from magicdispel import names

# Names as screenshots, phone cameras and chat apps write them.
CASES = [
    ("Screenshot 2026-09-23 at 15.14.15", "Screenshot"),
    ("Screenshot 2026-09-23 at 15.14.15 (2)", "Screenshot (2)"),
    ("Screen Shot 2021-05-04 at 10.23.45 AM", "Screen Shot"),
    ("截屏2026-09-23 下午3.14.15", "截屏"),
    ("屏幕快照 2019-05-04 上午10.23.45", "屏幕快照"),
    ("Bildschirmfoto 2024-05-01 um 12.34.56", "Bildschirmfoto"),
    ("Captura de Tela 2024-05-01 às 12.34.56", "Captura de Tela"),
    ("スクリーンショット 2024-05-01 午後3.14.15", "スクリーンショット"),
    ("스크린샷 2024-05-01 오후 3.14.15", "스크린샷"),
    ("Screenshot 2024-05-01 123456", "Screenshot"),                    # Windows 11
    ("Screenshot from 2024-05-01 12-34-56", "Screenshot from"),        # GNOME
    ("Screenshot_20240501-123456", "Screenshot"),                      # Android
    ("Screenshot_2024-05-01-12-34-56-789_com.tencent.mm", "Screenshot_com.tencent.mm"),
    ("IMG_20240501_123456", "IMG"),
    ("IMG_20240501_123456_123", "IMG"),
    ("PXL_20240501_123456789.MP", "PXL.MP"),                           # Pixel motion photo
    ("20240501_123456", ""),                                           # Samsung
    ("IMG-20240501-WA0001", "IMG-WA0001"),                             # WhatsApp
    ("photo_2024-05-01_12-34-56", "photo"),                            # Telegram
    ("signal-2024-05-01-123456", "signal"),
    ("mmexport1714567890123", "mmexport"),                             # WeChat
    ("wx_camera_1714567890123", "wx_camera"),
    ("微信图片_20240501123456", "微信图片"),
    ("QQ图片20240501123456", "QQ图片"),
    ("Snipaste_2024-05-01_12-34-56", "Snipaste"),
    ("2024年5月1日 家庭聚会", "家庭聚会"),
    ("家庭聚会 01.05.2024", "家庭聚会"),
    ("Foto 2024-05-01 kello 12.34.56", "Foto kello"),                  # an unlisted word: the time still goes
]
UNCHANGED = ["IMG_1234", "DSC_0001", "photo one", "app-v2.10.15", "IMG_20241350", "姓名_报告", "13812345678"]


class NameTests(unittest.TestCase):
    def test_dates_times_and_timestamps_are_removed(self):
        for stem, expected in CASES:
            with self.subTest(stem=stem):
                self.assertEqual(names.without_dates(stem), expected)

    def test_other_names_are_left_alone(self):
        for stem in UNCHANGED:
            with self.subTest(stem=stem):
                self.assertEqual(names.without_dates(stem), stem)

    def test_candidates(self):
        def first(stem, naming, count=3):
            generated = names.candidates(stem, ".png", naming)
            return [next(generated) for _ in range(count)]

        self.assertEqual(first("Screenshot 2026-09-23 at 15.14.15", "plain"),
                         ["Screenshot_clean.png", "Screenshot_clean_1.png", "Screenshot_clean_2.png"])
        self.assertEqual(first("20240501_123456", "plain", 1), ["photo_clean.png"])
        self.assertEqual(first("Screenshot 2026-09-23 at 15.14.15", "original", 1),
                         ["Screenshot 2026-09-23 at 15.14.15_clean.png"])
        self.assertRegex(first("IMG_1234", "anonymous", 1)[0], r"^photo_[0-9a-f]{32}\.png$")


if __name__ == "__main__":
    unittest.main()
