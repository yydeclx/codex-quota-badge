import time
import unittest
from PIL import Image

from native_badge import Win
from sidebar_styles import preview
from quota_styles import QUOTA_STYLES, quota_height, quota_value_color, quota_label, ALERT_COLOR
from quota_styles import _dual_image


class DualQuotaStyleTests(unittest.TestCase):
    def test_new_styles_are_registered_without_changing_selection(self):
        self.assertEqual(list(QUOTA_STYLES)[-6:], ['12','13','14','15','16','17'])
        self.assertEqual(quota_height('11'), 34)
        self.assertEqual(quota_height('15'), 34)

    def test_each_new_style_matches_its_declared_height(self):
        for code in ('12','13','14','15','16','17'):
            image=preview(code,2,value='58',quota=True)
            self.assertEqual(image.size,(68,quota_height(code)*2),code)

    def test_unknown_and_extreme_values_render_without_failure(self):
        for code in ('12','13','14','15','16','17'):
            for value in (None,0,36,58,100):
                image=preview(code,3,value=value,quota=True)
                self.assertEqual(image.size,(102,quota_height(code)*3))

    def test_new_styles_are_near_opaque_and_have_content(self):
        # Preview uses the same native renderer as the manager tiles.
        for code in ('12','13','14','15','16','17'):
            image=preview(code,3,value=58,quota=True)
            self.assertIsInstance(image,Image.Image)
            self.assertGreater(len(set(image.get_flattened_data())),20,code)

    def test_style_11_alert_threshold_is_strictly_below_twenty(self):
        normal=(76,75,73)
        self.assertEqual(quota_value_color(14,normal),ALERT_COLOR)
        self.assertEqual(quota_value_color(19.9,normal),ALERT_COLOR)
        self.assertEqual(quota_value_color(20,normal),normal)
        self.assertEqual(quota_value_color(55,normal),normal)

    def test_style_12_remains_monochrome(self):
        image=_dual_image('12',3,14,14,(243,242,240))
        red=sum(1 for r,g,b in image.get_flattened_data() if r>150 and r>g*1.35 and r>b*1.25)
        self.assertEqual(red,0)

    def test_style_11_shows_full_quota_as_large_plain_100(self):
        self.assertEqual(quota_label(100),'100')
        self.assertEqual(quota_label(99),'99%')
        self.assertEqual(quota_label(None),'—')


if __name__=='__main__':unittest.main()
