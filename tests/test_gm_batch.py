import sqlite3
import tempfile
import unittest
from pathlib import Path

import mercantile
import numpy as np
from osgeo import gdal, osr
from PIL import Image
from shapely.geometry import box

import geo_common as common
from gm_batch import check_disk_for_county, gm_script, make_mbtiles, verify_output


class GmBatchTest(unittest.TestCase):
    def test_gm_script_uses_confirmed_defaults_and_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            script, _ = gm_script(root, root / 'input.mbtiles', root / 'boundary.geojson',
                                  root / '652829_博湖县_default.tif', 32645)
            content = script.read_text(encoding='utf-8')
            for setting in ('PALETTE=OPTIMIZED', 'COMPRESSION=PACKBITS',
                            'SPATIAL_RES="5,5"', 'BG_TRANSPARENT=NO',
                            'ADD_OVERVIEW_LAYERS=NO', 'OVERWRITE_EXISTING=NO'):
                self.assertIn(setting, content)
            self.assertNotIn('TILE_SIZE=', content)
            self.assertIn('652829_博湖县_default.tif', content)

    def test_mbtiles_pack_and_resume_without_duplicate_tiles(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cfg = {'cache_dir': str(root / 'cache'), 'custom_url': 'https://example/{z}/{y}/{x}',
                   'source_crs': 'EPSG:3857', 'tile_size': 256, 'zoom': 16}
            tiles = [mercantile.Tile(100, 200, 16), mercantile.Tile(101, 200, 16)]
            for tile in tiles:
                path = common.tile_path(cfg, tile)
                path.parent.mkdir(parents=True, exist_ok=True)
                Image.new('RGB', (256, 256), (10, 20, 30)).save(path)
            output = root / 'tiles.mbtiles'
            self.assertEqual(make_mbtiles(output, tiles, cfg, box(80, 40, 81, 41)), output)
            self.assertEqual(make_mbtiles(output, tiles, cfg, box(80, 40, 81, 41)), output)
            conn = sqlite3.connect(output)
            try:
                self.assertEqual(conn.execute('SELECT COUNT(*) FROM tiles').fetchone()[0], 2)
                schema = conn.execute("SELECT sql FROM sqlite_master WHERE name='tiles'").fetchone()[0]
                self.assertNotIn('WITHOUT ROWID', schema.upper())
                self.assertEqual(conn.execute('SELECT tile_row FROM tiles LIMIT 1').fetchone()[0],
                                 (1 << 16) - 1 - 200)
            finally:
                conn.close()

    def test_disk_plan_counts_missing_tiles(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cfg = {'cache_dir': str(root / 'cache'), 'custom_url': 'https://example/{z}/{y}/{x}',
                   'source_crs': 'EPSG:3857', 'tile_size': 256, 'zoom': 16}
            tiles = [mercantile.Tile(100, 200, 16), mercantile.Tile(101, 200, 16)]
            path = common.tile_path(cfg, tiles[0])
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'cache')
            plan = check_disk_for_county(root, cfg, tiles)
            self.assertEqual(plan['missing_tiles'], 1)

    def test_verify_palette_packbits_and_full_readback(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / 'test.tif'
            driver = gdal.GetDriverByName('GTiff')
            ds = driver.Create(str(output), 32, 24, 1, gdal.GDT_Byte,
                               options=['COMPRESS=PACKBITS'])
            ds.SetGeoTransform((500000, 5, 0, 4600000, 0, -5))
            srs = osr.SpatialReference()
            srs.ImportFromEPSG(32644)
            ds.SetSpatialRef(srs)
            palette = gdal.ColorTable()
            for i in range(256):
                palette.SetColorEntry(i, (i, i, i, 255))
            band = ds.GetRasterBand(1)
            band.SetRasterColorTable(palette)
            band.WriteArray(np.full((24, 32), 7, dtype=np.uint8))
            ds = None
            result = verify_output(output, 32644)
            self.assertTrue(result['full_readback_verified'])
            self.assertEqual(result['bands'], 1)
            self.assertEqual(result['colors'], 256)
            self.assertEqual(result['compression'], 'PACKBITS')


if __name__ == '__main__':
    unittest.main()
