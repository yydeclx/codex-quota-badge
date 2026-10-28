import unittest
from PIL import Image
from sidebar_transparency import from_mattes, retain_row_hits


class TransparencyTests(unittest.TestCase):
    def test_empty_background_has_zero_alpha(self):
        result=from_mattes(Image.new('RGB',(3,3),'black'),Image.new('RGB',(3,3),'white'),(3,3))
        self.assertEqual(result.mode,'RGBa')
        self.assertEqual(result.getpixel((1,1)),(0,0,0,0))

    def test_opaque_glyph_and_antialias_are_premultiplied(self):
        black=Image.new('RGB',(2,1));white=Image.new('RGB',(2,1))
        black.putdata([(76,75,73),(38,37,36)])
        white.putdata([(76,75,73),(165,164,163)])
        result=from_mattes(black,white,(2,1))
        self.assertEqual(result.getpixel((0,0)),(76,75,73,255))
        self.assertEqual(result.getpixel((1,0)),(38,37,36,128))
        # Composite the same immutable pixels against light/dim/dark parents.
        for bg in (243,213,32):
            output=Image.alpha_composite(Image.new('RGBA',(2,1),(bg,bg,bg,255)),result.convert('RGBA'))
            self.assertAlmostEqual(output.getpixel((1,0))[0],38+bg*127/255,delta=1)

    def test_hit_surface_retains_rows_but_not_gaps(self):
        blank=Image.new('RGBa',(6,10))
        result=retain_row_hits(blank,(0,20,6,30),[(18,24),(27,40)])
        self.assertEqual(result.getpixel((3,2)),(1,1,1,1))
        self.assertEqual(result.getpixel((3,5)),(0,0,0,0))
        self.assertEqual(result.getpixel((3,8)),(1,1,1,1))
        # Minimal white alpha rounds to the original light rail color.
        for color in (243,213,180):
            output=Image.alpha_composite(Image.new('RGBA',result.size,(color,color,color,255)),result.convert('RGBA'))
            self.assertEqual(output.getpixel((3,2)),(color,color,color,255))

    def test_hit_surface_preserves_existing_ink(self):
        image=Image.new('RGBa',(4,4),(38,37,36,128))
        result=retain_row_hits(image,(0,0,4,4),[(0,4)])
        self.assertEqual(result.tobytes(),image.tobytes())

    def test_invalid_colored_edges_cannot_exceed_alpha(self):
        result=from_mattes(Image.new('RGB',(3,3),(40,80,90)),Image.new('RGB',(3,3),(240,240,240)),(1,1))
        *rgb,alpha=result.getpixel((0,0))
        self.assertTrue(all(c<=alpha for c in rgb))


if __name__=='__main__':unittest.main()
