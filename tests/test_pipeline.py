import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import geo_common as common
import imagery
import tasks
import quark_backend
import xinjiang_batch
import cleanup_cache
import tempfile
import unittest
import zipfile
import json
import numpy as np
from unittest.mock import patch
from PIL import Image
from shapely.geometry import box

class PipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cfg=common.load_config()
        cls.df=common.cities(cls.cfg)

    def test_exact_city_and_unknown_fail(self):
        self.assertEqual(common.match_city(self.df,'威海').ct_name,'威海市')
        with self.assertRaises(ValueError): common.match_city(self.df,'海')

    def test_source_cache_isolation(self):
        import mercantile
        c=dict(self.cfg); c['custom_url']+='?different-release'
        t=mercantile.Tile(1,1,16)
        self.assertNotEqual(common.tile_path(c,t),common.tile_path(self.cfg,t))

    def test_processing_tuning_keeps_artifact_identity(self):
        changed=dict(self.cfg,processing_threads=12,warp_memory_mb=1024,gdal_cache_mb=1024,
                     mosaic_driver='vrt')
        self.assertEqual(common.job_config_hash(self.cfg), common.job_config_hash(changed))

    def test_corrupt_tile_not_reused(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'tile.png';p.write_text('<html>error</html>')
            self.assertFalse(imagery.validate_tile(p))
            Image.new('RGB',(256,256)).save(p)
            self.assertTrue(imagery.validate_tile(p))

    def test_archive_selection_and_traversal(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'input.zip'
            with zipfile.ZipFile(p,'w') as z:
                z.writestr('山东省/威海市.txt','data')
                z.writestr('辽宁省/大连市.txt','do not select')
            cfg=dict(self.cfg,jobs_dir=str(Path(tmp)/'jobs'))
            path,plan=tasks.make_plan(cfg,archive=p,selected=['山东省/威海市.txt'])
            self.assertEqual([j['city'] for j in plan['jobs']],['威海市'])
            tasks.validate_plan(path,cfg)
            with self.assertRaises(ValueError): tasks.make_plan(cfg,archive=p)
            with zipfile.ZipFile(p,'a') as z:z.writestr('../bad.txt','x')
            with self.assertRaises(ValueError):tasks.zip_entries(p)
            with self.assertRaises(ValueError):tasks.validate_plan(path,cfg)

    def test_palette_block_boundary_invariance(self):
        rng=np.random.default_rng(42)
        rgb=rng.integers(0,256,(73,129,3),dtype=np.uint8)
        pal=imagery.make_palette(rgb.reshape(-1,3))
        full=imagery.map_palette(rgb,pal)
        pieces=np.concatenate([imagery.map_palette(rgb[:,:61],pal),
                               imagery.map_palette(rgb[:,61:],pal,61,0)],axis=1)
        np.testing.assert_array_equal(full,pieces)

    def test_palette_preserves_rare_bright_neutral_colors(self):
        rng=np.random.default_rng(7)
        desert=rng.integers([110,90,60],[225,190,145],size=(20000,3),dtype=np.uint8)
        snow=np.array([[210,216,217],[225,229,231],[240,242,243],
                       [250,251,252]],dtype=np.uint8)
        pixels=np.concatenate([desert,np.repeat(snow,20,axis=0)])
        palette=imagery.make_palette(pixels)
        colors=np.array(palette.getpalette(),dtype=np.uint8).reshape(256,3)
        mapped=colors[imagery.map_palette(snow.reshape(1,-1,3),palette,strength=0)[0]]
        self.assertLess(np.abs(snow.astype(np.int16)-mapped.astype(np.int16)).mean(),5)

    def test_palette_preserves_rare_blue_ice(self):
        rng=np.random.default_rng(8)
        desert=rng.integers([110,90,60],[225,190,145],size=(20000,3),dtype=np.uint8)
        ice=np.array([[60,100,142],[82,120,165],[110,150,190]],dtype=np.uint8)
        pixels=np.concatenate([desert,np.repeat(ice,40,axis=0)])
        palette=imagery.make_palette(pixels)
        colors=np.array(palette.getpalette(),dtype=np.uint8).reshape(256,3)
        mapped=colors[imagery.map_palette(ice.reshape(1,-1,3),palette,strength=0)[0]]
        self.assertLess(np.abs(ice.astype(np.int16)-mapped.astype(np.int16)).mean(),8)

    def test_quark_false_success_rejected(self):
        for text,code in [('',0),('not json',0),
            (json.dumps({'type':'result','code':-204,'msg':'failed','data':{}}),0),
            (json.dumps({'type':'result','code':0,'data':{}}),1)]:
            with self.assertRaises(RuntimeError):quark_backend.parse_result(text,code)

    def test_missing_tile_stops_processing(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg=dict(self.cfg,cache_dir=tmp)
            geom=box(122.11,37.50,122.112,37.502)
            with self.assertRaises(RuntimeError):
                imagery.build_vrt(cfg,common.expected_tiles(geom,16),Path(tmp))

    def test_gti_matches_vrt_on_sparse_tiles(self):
        import mercantile
        from osgeo import gdal
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            cfg=dict(self.cfg,cache_dir=str(root/'tiles'))
            tiles=[mercantile.Tile(48497,24045,16),
                   mercantile.Tile(48498,24045,16),
                   mercantile.Tile(48497,24046,16)]
            for i,tile in enumerate(tiles):
                path=common.tile_path(cfg,tile)
                path.parent.mkdir(parents=True,exist_ok=True)
                Image.new('RGB',(256,256),(30+i*30,80+i*20,140+i*10)).save(path)
            vrt_dir=root/'vrt'; vrt_dir.mkdir()
            gti_dir=root/'gti'; gti_dir.mkdir()
            vrt=imagery.build_vrt(cfg,tiles,vrt_dir)
            gti=imagery.build_gti(cfg,tiles,gti_dir)
            v=gdal.Open(str(vrt))
            t=gdal.Open(str(gti))
            self.assertEqual((v.RasterXSize,v.RasterYSize),(t.RasterXSize,t.RasterYSize))
            try:
                np.testing.assert_allclose(v.GetGeoTransform(),t.GetGeoTransform(),atol=1e-8)
                a=v.ReadAsArray()
                b=t.ReadAsArray()
                np.testing.assert_array_equal(a[:3],b[:3])
                np.testing.assert_array_equal(a[3]>0,t.GetRasterBand(1).GetMaskBand().ReadAsArray()>0)
            finally:
                v=None
                t=None

    def test_download_errors_are_not_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg=dict(self.cfg,cache_dir=tmp,jobs_dir=tmp,retries=1)
            with patch('requests.Session.get',side_effect=imagery.requests.ConnectionError('offline')):
                with self.assertRaises(RuntimeError):imagery.download(cfg,box(122.11,37.50,122.111,37.501))
            self.assertTrue((Path(tmp)/'download_failures.json').exists())

    def test_spatial_filename_conflict(self):
        from shapely.geometry import mapping
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'conflict.zip'
            content=json.dumps({'type':'FeatureCollection','features':[{'type':'Feature','properties':{},
                 'geometry':mapping(box(122.10,37.49,122.101,37.491))}]})
            with zipfile.ZipFile(p,'w') as z:z.writestr('大连市.geojson',content)
            with self.assertRaises(ValueError):tasks.resolve_file(p,'大连市.geojson',self.cfg,self.df,tmp)

    def test_spatial_only_identification(self):
        from shapely.geometry import mapping
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'spatial.zip'
            content=json.dumps({'type':'FeatureCollection','features':[{'type':'Feature','properties':{},
                 'geometry':mapping(box(122.10,37.49,122.101,37.491))}]})
            with zipfile.ZipFile(p,'w') as z:z.writestr('区域001.geojson',content)
            row,evidence=tasks.resolve_file(p,'区域001.geojson',self.cfg,self.df,tmp)
            self.assertEqual(row.ct_name,'威海市')

    def test_cloud_receipt_is_not_blindly_reuploaded(self):
        with tempfile.TemporaryDirectory() as tmp:
            f=Path(tmp)/'sample.bin';f.write_bytes(b'abc')
            receipt=Path(tmp)/'receipt.json'
            common.write_json(receipt,{'local_sha256':common.digest_file(f),'parent_fid':'chosen', 'fid':'old'})
            cfg=dict(self.cfg,quark=dict(self.cfg['quark'],parent_fid='chosen'))
            q=quark_backend.Quark(cfg,'test','1-abcdef')
            with patch.object(q,'verify',return_value=False),patch.object(q,'call') as call:
                with self.assertRaises(RuntimeError):q.upload(f,receipt)
                call.assert_not_called()

    def test_cloud_listing_accepts_unique_opaque_fid(self):
        with tempfile.TemporaryDirectory() as tmp:
            f=Path(tmp)/'sample.bin'; f.write_bytes(b'abc')
            receipt=Path(tmp)/'receipt.json'
            common.write_json(receipt,{'local_sha256':common.digest_file(f),
                                       'parent_fid':'chosen','fid':'upload-fid'})
            cfg=dict(self.cfg,quark=dict(self.cfg['quark'],parent_fid='chosen'))
            q=quark_backend.Quark(cfg,'test','1-abcdef')
            listing=[{'fid':'opaque-list-fid','filename':f.name,'size':f.stat().st_size}]
            with patch.object(q,'browse',return_value=listing),patch.object(q,'call') as call:
                result=q.upload(f,receipt)
                call.assert_not_called()
            self.assertTrue(result['verified'])
            self.assertEqual(result['cloud_listing_fid'],'opaque-list-fid')

    def test_batch_status_survives_resume(self):
        with tempfile.TemporaryDirectory() as tmp:
            row={'code':'652302','下载状态':'等待','压制状态':'等待',
                 '上传状态':'等待','文件序号':1,'源文件':'19_23.shp','城市':'阜康市','last_update':'','备注':''}
            first=xinjiang_batch.Status(tmp,[row])
            first.update('652302',下载状态='已下载')
            resumed=xinjiang_batch.Status(tmp,[row])
            self.assertEqual(resumed.rows['652302']['下载状态'],'已下载')

    def test_resume_requires_visual_review(self):
        with tempfile.TemporaryDirectory() as tmp:
            work=Path(tmp)
            result=work/'result.tif'
            result.write_bytes(b'complete')
            preview=work/'comparison.png'
            preview.write_bytes(b'preview')
            artifact={'path':str(result),'size':result.stat().st_size,
                      'sha256':common.digest_file(result),'comparisons':[str(preview)]}
            common.write_json(work/'state.json',{'artifact':artifact})
            common.write_json(work/'quality.json',{'passed':True,'full_readback_verified':True})
            self.assertIsNone(xinjiang_batch.valid_existing(work/'state.json'))
            common.write_json(work/'visual_review.json',{'result':'pass'})
            self.assertEqual(xinjiang_batch.valid_existing(work/'state.json'),artifact)

    def test_cleanup_preserves_tiles_in_pending_area(self):
        import mercantile
        tile=mercantile.Tile(48497,24045,16)
        bounds=mercantile.bounds(tile)
        self.assertTrue(cleanup_cache.overlaps_pending(tile,[bounds]))
        self.assertFalse(cleanup_cache.overlaps_pending(tile,[(0,0,1,1)]))

    def test_cleanup_requires_cloud_and_visual_verification(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); work=root/'work'; work.mkdir()
            output=root/'out'; output.mkdir()
            artifact_path=output/'result.tif'; artifact_path.write_bytes(b'complete')
            artifact={'path':str(artifact_path),'size':artifact_path.stat().st_size,
                      'sha256':common.digest_file(artifact_path),
                      'comparisons':[str(work/'comparison.png')]}
            (work/'comparison.png').write_bytes(b'preview')
            common.write_json(work/'state.json',{'status':'processed','artifact':artifact})
            common.write_json(work/'quality.json',{'passed':True,'full_readback_verified':True})
            common.write_json(work/'visual_review.json',{'result':'pass'})
            receipt={'verified':True,'verification':'fresh_listing_fid_name_size',
                     'cloud_listing_fid':'cloud-id','local_sha256':artifact['sha256'],
                     'size':artifact['size'],'parent_fid':'folder'}
            common.write_json(work/'upload_receipt.json',receipt)
            row={'压制状态':'已压制（质检通过）','上传状态':'已上传并核验'}
            self.assertTrue(cleanup_cache.verified_row(row,work,output,'folder'))
            receipt['verified']=False
            common.write_json(work/'upload_receipt.json',receipt)
            self.assertFalse(cleanup_cache.verified_row(row,work,output,'folder'))

    def test_synthetic_geotiff_full_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg=dict(self.cfg,cache_dir=str(Path(tmp)/'tiles'),output_dir=str(Path(tmp)/'out'))
            geom=box(122.11,37.50,122.114,37.504)
            tiles=common.expected_tiles(geom,16)
            for tile in tiles:
                p=common.tile_path(cfg,tile);p.parent.mkdir(parents=True,exist_ok=True)
                yy,xx=np.mgrid[:256,:256]
                rgb=np.stack([80+xx//4,80+yy//4,60+(xx+yy)//8],axis=-1).astype(np.uint8)
                rgb[:64,:64]=[45,88,135]
                Image.fromarray(rgb).save(p)
            artifact=imagery.process(cfg,geom,'测试','test',Path(tmp)/'work',tiles)
            self.assertEqual(artifact['epsg'],32651)
            self.assertTrue(artifact['quality']['passed'])
            self.assertTrue(artifact['quality']['full_readback_verified'])
            self.assertGreater(artifact['quality']['cool_region_pixels'],0)
            self.assertIsInstance(artifact['quality']['passed'],bool)
            self.assertIsInstance(artifact['quality']['cool_region_mae_dn'],float)
            self.assertTrue(json.loads((Path(tmp)/'work'/'quality.json').read_text(encoding='utf-8'))['passed'])
            self.assertEqual(common.digest_file(artifact['path']),artifact['sha256'])
            reference=Path(tmp)/'work'/'reference_rgb.tif'
            with self.assertRaisesRegex(RuntimeError,'哈希不符'):
                imagery.process(cfg,geom,'测试','test',Path(tmp)/'work',
                                reference_sha256='0'*64)
            recolored=imagery.process(cfg,geom,'测试','test',Path(tmp)/'work',
                                      reference_sha256=common.digest_file(reference))
            self.assertEqual(artifact['sha256'],recolored['sha256'])

if __name__=='__main__':unittest.main(verbosity=2)
