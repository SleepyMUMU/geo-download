"""Validated XYZ download and one-pass UTM warp followed by city-wide palette mapping."""
import geo_common as common
import io
import math
import os
import sqlite3
import threading
import time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import requests
import mercantile
from PIL import Image
from osgeo import gdal, ogr, osr

def validate_tile(path):
    try:
        with Image.open(path) as im:
            im.load()
            return im.size == (256, 256) and im.mode in ('RGB', 'RGBA') and (
                im.mode != 'RGBA' or im.getextrema()[3] == (255, 255))
    except (OSError, ValueError):
        return False

def download(cfg, geom):
    tiles = common.expected_tiles(geom, cfg['zoom'])
    if not tiles:
        raise ValueError('没有相交瓦片')
    local = threading.local()
    def fetch(tile):
        path = common.tile_path(cfg, tile)
        if validate_tile(path):
            return None
        if not hasattr(local, 'session'):
            local.session = requests.Session()
            local.session.trust_env = False
            if cfg.get('proxy'):
                local.session.proxies.update({'http': cfg['proxy'], 'https': cfg['proxy']})
        url = cfg['custom_url'].format(x=tile.x, y=tile.y, z=tile.z)
        error = ''
        for attempt in range(cfg.get('retries', 3)):
            try:
                response = local.session.get(url, timeout=(15, 60))
                response.raise_for_status()
                with Image.open(io.BytesIO(response.content)) as src:
                    src.load()
                    if src.size != (256, 256) or src.mode not in ('RGB', 'RGBA'):
                        raise ValueError('瓦片尺寸/色彩类型异常')
                    if src.mode == 'RGBA' and src.getextrema()[3] != (255, 255):
                        raise ValueError('图源返回透明或无数据瓦片')
                    path.parent.mkdir(parents=True, exist_ok=True)
                    partial = path.with_suffix('.partial')
                    src.convert('RGB').save(partial, format='PNG')
                os.replace(partial, path)
                return None
            except (requests.RequestException, OSError, ValueError) as exc:
                error = type(exc).__name__ + ': ' + str(exc)[:160]
                if attempt + 1 < cfg.get('retries', 3):
                    time.sleep(min(2 ** attempt, 8))
        return {'tile': list(tile), 'error': error}
    failures = []
    with ThreadPoolExecutor(max_workers=cfg.get('max_threads', 8)) as pool:
        for i, result in enumerate(pool.map(fetch, tiles), 1):
            if result:
                failures.append(result)
            if i % max(1, len(tiles) // 20) == 0:
                print(f'下载 {i}/{len(tiles)}，失败 {len(failures)}', flush=True)
    if failures:
        report = Path(cfg['jobs_dir']) / 'download_failures.json'
        common.write_json(report, failures)
        raise RuntimeError(f'{len(failures)} 块瓦片失败，禁止拼接。详情: {report}')
    return tiles

def prepared_tiles(cfg, tiles):
    """Validate each cached PNG and attach Web Mercator world-file coordinates."""
    for tile in tiles:
        path = common.tile_path(cfg, tile)
        if not validate_tile(path):
            raise RuntimeError(f'缺失或损坏瓦片: {path}')
        left, bottom, right, top = mercantile.xy_bounds(tile)
        a, e = (right-left)/256, (bottom-top)/256
        path.with_suffix('.pgw').write_text(
            f'{a:.15f}\n0\n0\n{e:.15f}\n{left+a/2:.15f}\n{top+e/2:.15f}\n', encoding='ascii')
        yield path, (left, bottom, right, top)

def build_vrt(cfg, tiles, work):
    files = [str(path) for path, _ in prepared_tiles(cfg, tiles)]
    vrt = work / 'mosaic.vrt'
    ds = gdal.BuildVRT(str(vrt), files, options=gdal.BuildVRTOptions(
        outputSRS='EPSG:3857', addAlpha=True, strict=True))
    if ds is None:
        raise RuntimeError('VRT 构建失败')
    ds = None
    return vrt

def build_gti(cfg, tiles, work):
    """Build a spatially indexed GTI mosaic for very large XYZ tile sets."""
    if gdal.GetDriverByName('GTI') is None:
        raise RuntimeError('当前 GDAL 未提供 GTI 驱动')
    index = Path(work) / 'mosaic.gti.gpkg'
    partial = index.with_name('mosaic.partial.gti.gpkg')

    def checked_layer(path, update=False):
        source = ogr.Open(str(path), int(update))
        layer = source.GetLayerByName('tiles') if source else None
        if layer is None:
            raise RuntimeError(f'GTI 索引图层缺失，保留文件: {path}')
        # GeoPackage's cached feature_count can remain zero after interruption
        # even when committed tiles are present. Query the actual table rows.
        db = sqlite3.connect(f'file:{path}?mode=ro', uri=True)
        try:
            count, minimum, maximum = db.execute(
                'SELECT COUNT(*), MIN(fid), MAX(fid) FROM tiles').fetchone()
        finally:
            db.close()
        if count < 0 or count > len(tiles) or (count and (minimum != 1 or maximum != count)):
            raise RuntimeError(f'GTI 索引进度不可信，保留文件: {path}')
        if (layer.GetMetadataItem('BAND_COUNT') != '3'
                or layer.GetMetadataItem('MASK_BAND') != 'YES'):
            raise RuntimeError(f'GTI 索引元数据不符，保留文件: {path}')
        for fid in sorted({1, count // 4, count // 2, count * 3 // 4, count} - {0} if count else ()):
            feature = layer.GetFeature(fid)
            expected = str(common.tile_path(cfg, tiles[fid-1]))
            if feature is None or feature.GetField('location') != expected:
                raise RuntimeError(f'GTI 索引与本次瓦片清单不符，保留文件: {path}')
        return source, layer, count

    if index.exists():
        source, layer, count = checked_layer(index)
        source = layer = None
        if count != len(tiles):
            raise RuntimeError(f'GTI 已完成索引条目不足，保留文件: {index}')
        mosaic = gdal.OpenEx(str(index), gdal.OF_RASTER, allowed_drivers=['GTI'])
        if mosaic is None or mosaic.RasterCount != 3:
            raise RuntimeError(f'GTI 已完成索引回读失败，保留文件: {index}')
        mosaic = None
        return index

    if partial.exists():
        ds, layer, start = checked_layer(partial, update=True)
        print(f'GTI 索引续写 {start}/{len(tiles)} 条', flush=True)
    else:
        start = 0
        ds = ogr.GetDriverByName('GPKG').CreateDataSource(str(partial))
        if ds is None:
            raise RuntimeError('GTI 索引创建失败')
        srs = osr.SpatialReference()
        srs.ImportFromEPSG(3857)
        layer = ds.CreateLayer('tiles', srs=srs, geom_type=ogr.wkbPolygon,
                               options=['SPATIAL_INDEX=YES'])
        layer.CreateField(ogr.FieldDefn('location', ogr.OFTString))
        resolution = 2 * math.pi * 6378137 / (2 ** int(cfg['zoom']) * 256)
        for key, value in {'RESX': resolution, 'RESY': resolution,
                           'BAND_COUNT': 3, 'DATA_TYPE': 'Byte',
                           'COLOR_INTERPRETATION': 'red,green,blue',
                           'MASK_BAND': 'YES'}.items():
            layer.SetMetadataItem(key, str(value))
    if ds is None:
        raise RuntimeError('GTI 索引创建失败')
    try:
        layer.StartTransaction()
        for count, (path, (left, bottom, right, top)) in enumerate(prepared_tiles(cfg, tiles[start:]), start+1):
            ring = ogr.Geometry(ogr.wkbLinearRing)
            for x, y in ((left, bottom), (right, bottom), (right, top),
                         (left, top), (left, bottom)):
                ring.AddPoint_2D(x, y)
            polygon = ogr.Geometry(ogr.wkbPolygon)
            polygon.AddGeometry(ring)
            feature = ogr.Feature(layer.GetLayerDefn())
            feature.SetFID(count)
            feature.SetGeometry(polygon)
            feature.SetField('location', str(path))
            if layer.CreateFeature(feature) != ogr.OGRERR_NONE:
                raise RuntimeError(f'GTI 索引写入失败: {path}')
            feature = None
            if count % 5000 == 0:
                layer.CommitTransaction()
                layer.StartTransaction()
        layer.CommitTransaction()
    finally:
        layer = ds = None
    db = sqlite3.connect(partial)
    try:
        db.execute('UPDATE gpkg_ogr_contents SET feature_count=? WHERE table_name=?',
                   (len(tiles), 'tiles'))
        db.commit()
    finally:
        db.close()
    os.replace(partial, index)
    mosaic = gdal.OpenEx(str(index), gdal.OF_RASTER, allowed_drivers=['GTI'])
    if mosaic is None or mosaic.RasterCount != 3:
        raise RuntimeError('GTI 索引回读失败')
    mosaic = None
    return index

def build_mosaic(cfg, tiles, work):
    driver = cfg.get('mosaic_driver', 'vrt').lower()
    if driver == 'gti':
        return build_gti(cfg, tiles, work)
    if driver == 'vrt':
        return build_vrt(cfg, tiles, work)
    raise ValueError(f'不支持的拼接驱动: {driver}')

def sample_rgb(ds, limit=262144):
    # Spatially distributed, bounded-memory training set; no full-city palette search.
    ratio = min(1, math.sqrt(limit/(ds.RasterXSize*ds.RasterYSize)))
    w, h = max(1, int(ds.RasterXSize*ratio)), max(1, int(ds.RasterYSize*ratio))
    data = ds.ReadAsArray(buf_xsize=w, buf_ysize=h)
    valid = data[3] > 0
    pixels = data[:3].transpose(1, 2, 0)[valid]
    if len(pixels) == 0:
        raise RuntimeError('重投影结果没有有效像元')
    return pixels

def palette_pixels(ds, limit=262144):
    """Expand sparse cool-color samples before training the shared palette."""
    limit = int(limit)
    while True:
        pixels = sample_rgb(ds, limit)
        values = pixels.astype(np.int16)
        cool = ((values[:, 2] > values[:, 0] + 15)
                & (values[:, 2] > values[:, 1] + 8)
                & (values.mean(axis=1) > 35))
        count = int(cool.sum())
        if count == 0 or count >= 64 or limit >= 4194304:
            return pixels
        limit = min(limit * 4, 4194304)

def make_palette(pixels):
    # A city-wide median cut can lose rare snow and blue ice among desert pixels.
    values = pixels.astype(np.int16)
    neutral = (values.mean(axis=1) > 155) & (
        values.max(axis=1) - values.min(axis=1) < 12)
    cool = ((values[:, 2] > values[:, 0] + 15)
            & (values[:, 2] > values[:, 1] + 8)
            & (values.mean(axis=1) > 35))
    neutral_slots = 16 if neutral.sum() >= 16 else 0
    # Very sparse blue pixels can still cover tens of thousands of pixels in a
    # large county. Reserve only as many colors as the sample can train.
    cool_count = int(cool.sum())
    cool_slots = min(32, max(8, cool_count)) if cool_count >= 8 else 0

    def trained_colors(values, slots):
        trained = Image.fromarray(values.reshape(-1, 1, 3)).quantize(
            colors=slots, method=Image.Quantize.MEDIANCUT)
        colors = trained.getpalette()[:3 * slots]
        return colors + colors[-3:] * ((3 * slots - len(colors)) // 3)

    base_slots = 256 - neutral_slots - cool_slots
    colors = trained_colors(pixels, base_slots)
    if neutral_slots:
        colors += trained_colors(pixels[neutral], neutral_slots)
    if cool_slots:
        colors += trained_colors(pixels[cool], cool_slots)
    palette = Image.new('P', (1, 1))
    palette.putpalette(colors)
    return palette

def map_palette(rgb, palette, x=0, y=0, strength=4):
    # Global-coordinate ordered dither: deterministic across block boundaries.
    # Small perturbation reduces smooth-gradient banding without block-local error diffusion.
    matrix = np.array([[0,8,2,10],[12,4,14,6],[3,11,1,9],[15,7,13,5]], dtype=np.float32)
    if strength:
        yy = (np.arange(rgb.shape[0])+y) % 4
        xx = (np.arange(rgb.shape[1])+x) % 4
        delta = (matrix[yy[:, None], xx[None, :]]/16 - 0.46875)*strength
        rgb = np.clip(rgb.astype(np.float32)+delta[..., None], 0, 255).astype(np.uint8)
    return np.asarray(Image.fromarray(rgb).quantize(palette=palette, dither=Image.Dither.NONE))

def validate_output(path, epsg, expected=None):
    ds = gdal.Open(str(path))
    if ds is None or ds.RasterCount != 1 or ds.GetRasterBand(1).DataType != gdal.GDT_Byte:
        raise RuntimeError('成果不是单波段 8-bit')
    band = ds.GetRasterBand(1)
    ct = band.GetColorTable()
    srs = ds.GetSpatialRef()
    if ct is None or ct.GetCount() != 256:
        raise RuntimeError('成果缺少 256 色调色板')
    if srs.GetAuthorityCode(None) != str(epsg):
        raise RuntimeError('成果 CRS 不匹配')
    transform = ds.GetGeoTransform()
    if abs(transform[1]-5) > 1e-8 or abs(transform[5]+5) > 1e-8 or transform[2] or transform[4]:
        raise RuntimeError('成果像元不是 UTM 5×5 米')
    if band.GetOverviewCount() == 0 or not (band.GetMaskFlags() & gdal.GMF_PER_DATASET):
        raise RuntimeError('成果缺少金字塔或内部有效区掩膜')
    if ds.GetMetadata('IMAGE_STRUCTURE').get('COMPRESSION') != 'DEFLATE':
        raise RuntimeError('成果压缩格式异常')
    if expected and (ds.RasterXSize, ds.RasterYSize) != expected:
        raise RuntimeError('成果尺寸异常')
    return ds

def process(cfg, geom, city, code, work, tiles=None, reference_sha256=None):
    work = Path(work)
    work.mkdir(parents=True, exist_ok=True)
    processing_threads = int(cfg.get('processing_threads', 8))
    warp_memory_mb = int(cfg.get('warp_memory_mb', 512))
    gdal_cache_mb = int(cfg.get('gdal_cache_mb', 512))
    if not 1 <= processing_threads <= 64 or warp_memory_mb < 64 or gdal_cache_mb < 64:
        raise ValueError('GDAL 处理线程或缓存参数无效')
    # These settings affect this pipeline process only, not the Conda environment.
    gdal.SetConfigOption('GDAL_NUM_THREADS', str(processing_threads))
    gdal.SetCacheMax(gdal_cache_mb * 1024 * 1024)
    print(f'GDAL 处理: {processing_threads} 线程，warp {warp_memory_mb} MiB，缓存 {gdal_cache_mb} MiB', flush=True)
    epsg = common.utm_for(geom)
    rgb_path = work / 'reference_rgb.tif'
    if reference_sha256 is not None:
        if not rgb_path.is_file() or common.digest_file(rgb_path) != reference_sha256:
            raise RuntimeError('RGB 参考影像哈希不符，禁止复用')
        rgb = gdal.Open(str(rgb_path), gdal.GA_ReadOnly)
        if rgb is None or rgb.GetSpatialRef().GetAuthorityCode(None) != str(epsg):
            raise RuntimeError('RGB 参考影像投影不符，禁止复用')
        transform = rgb.GetGeoTransform()
        if abs(transform[1]-5)>1e-8 or abs(transform[5]+5)>1e-8 or transform[2] or transform[4]:
            raise RuntimeError('RGB 参考影像分辨率不符，禁止复用')
    else:
        tiles = tiles or common.expected_tiles(geom, cfg['zoom'])
        vrt = build_mosaic(cfg, tiles, work)
        from shapely.geometry import mapping
        cutline = work / 'boundary.geojson'
        common.write_json(cutline, {'type':'FeatureCollection', 'features':[
            {'type':'Feature', 'properties':{}, 'geometry':mapping(geom)}]})
        options = ['TILED=YES', 'COMPRESS=DEFLATE', 'PREDICTOR=2', 'ZLEVEL=6', 'BIGTIFF=YES',
                   f'NUM_THREADS={processing_threads}']
        rgb = gdal.Warp(str(rgb_path), str(vrt), options=gdal.WarpOptions(
            format='GTiff', dstSRS=f'EPSG:{epsg}', xRes=5, yRes=5,
            targetAlignedPixels=True, resampleAlg='average', outputType=gdal.GDT_Byte,
            cutlineDSName=str(cutline), cropToCutline=True, dstAlpha=True,
            creationOptions=options, warpMemoryLimit=warp_memory_mb,
            warpOptions=[f'NUM_THREADS={processing_threads}'], multithread=True))
    if rgb is None or rgb.RasterCount != 4:
        raise RuntimeError('UTM 重投影失败')
    rgb.FlushCache()
    pixels = palette_pixels(rgb, cfg.get('palette_samples', 262144))
    palette = make_palette(pixels)
    colors = np.array(palette.getpalette(), dtype=np.uint8).reshape(256, 3)
    version = common.fingerprint({'config':common.job_config_hash(cfg),'boundary':geom.wkb_hex})[:16]
    out = Path(cfg['output_dir']) / version / f'{city}_{code}_UTM{epsg}_5m_8bit.tif'
    out.parent.mkdir(parents=True, exist_ok=True)
    partial = out.with_suffix('.partial.tif')
    gdal.SetConfigOption('GDAL_TIFF_INTERNAL_MASK', 'YES')
    result = gdal.GetDriverByName('GTiff').Create(str(partial), rgb.RasterXSize, rgb.RasterYSize,
        1, gdal.GDT_Byte, options=['TILED=YES','COMPRESS=DEFLATE','ZLEVEL=6','BIGTIFF=YES',
                                   f'NUM_THREADS={processing_threads}'])
    result.SetGeoTransform(rgb.GetGeoTransform())
    result.SetProjection(rgb.GetProjection())
    result.SetMetadata({'SOURCE_URL':cfg['custom_url'], 'SOURCE_ZOOM':str(cfg['zoom']),
                        'COLOR_MODE':'256 color global palette; lossy quantization',
                        'RESAMPLING':'average', 'PALETTE_DITHER':'global ordered 4x4'})
    ct = gdal.ColorTable()
    for i, color in enumerate(colors):
        ct.SetColorEntry(i, tuple(map(int, color))+(255,))
    band = result.GetRasterBand(1)
    band.SetColorTable(ct)
    band.SetColorInterpretation(gdal.GCI_PaletteIndex)
    band.CreateMaskBand(gdal.GMF_PER_DATASET)
    mask = band.GetMaskBand()
    sq = 0.0
    abs_sum = 0.0
    n = 0
    valid_count = 0
    cool_abs_sum = 0.0
    cool_channels = 0
    histogram = np.zeros(256, dtype=np.int64)
    block = 512
    for y in range(0, rgb.RasterYSize, block):
        for x in range(0, rgb.RasterXSize, block):
            w, h = min(block, rgb.RasterXSize-x), min(block, rgb.RasterYSize-y)
            data = rgb.ReadAsArray(x, y, w, h)
            original = data[:3].transpose(1,2,0)
            valid = data[3] > 0
            indexed = map_palette(original, palette, x, y, cfg.get('dither_strength', 4))
            band.WriteArray(indexed, x, y)
            mask.WriteArray(valid.astype(np.uint8)*255, x, y)
            diff = colors[indexed][valid].astype(np.int16) - original[valid].astype(np.int16)
            source = original.astype(np.int16)
            cool = valid & (source[:, :, 2] > source[:, :, 0] + 15) & (source[:, :, 2] > source[:, :, 1] + 8) & (source.mean(axis=2) > 35)
            if np.any(cool):
                cool_abs_sum += float(np.abs(colors[indexed][cool].astype(np.int16) - source[cool]).sum())
                cool_channels += int(cool.sum()) * 3
            sq += np.square(diff.astype(np.float64)).sum()
            abs_sum += np.abs(diff).sum()
            n += diff.size
            valid_count += int(valid.sum())
            histogram += np.bincount(np.abs(diff).reshape(-1), minlength=256)
        print(f'调色板映射 {min(y+block, rgb.RasterYSize)}/{rgb.RasterYSize} 行', flush=True)
    if not n:
        raise RuntimeError('输出有效区为空')
    mae, rmse = abs_sum/n, math.sqrt(sq/n)
    p99 = int(np.searchsorted(np.cumsum(histogram), n*0.99))
    result.BuildOverviews('NEAREST', [2,4,8,16,32])
    result.FlushCache()
    mask = band = result = None
    checked = validate_output(partial, epsg, (rgb.RasterXSize, rgb.RasterYSize))
    checked = None
    # Full block readback verifies lossless palette indices and validity masks.
    checked = gdal.Open(str(partial))
    for y in range(0, rgb.RasterYSize, block):
        for x in range(0, rgb.RasterXSize, block):
            w,h = min(block,rgb.RasterXSize-x),min(block,rgb.RasterYSize-y)
            data=rgb.ReadAsArray(x,y,w,h)
            expected=map_palette(data[:3].transpose(1,2,0),palette,x,y,cfg.get('dither_strength',4))
            if not np.array_equal(checked.ReadAsArray(x,y,w,h),expected):
                raise RuntimeError('写盘回读像元不一致')
            if not np.array_equal(checked.GetRasterBand(1).GetMaskBand().ReadAsArray(x,y,w,h)>0,data[3]>0):
                raise RuntimeError('写盘回读掩膜不一致')
    checked = None
    quality = {'mae_dn':mae, 'rmse_dn':rmse, 'p99_abs_dn':p99,
               'cool_region_mae_dn':float(cool_abs_sum / cool_channels) if cool_channels else None,
               'cool_region_pixels':cool_channels // 3,
               'valid_pixels':valid_count, 'full_readback_verified':True,
               'thresholds':cfg['quality'], 'visual_approval_required':True}
    quality['passed'] = bool(mae <= cfg['quality']['max_mae_dn'] and p99 <= cfg['quality']['max_p99_dn']
                             and (not cool_channels or quality['cool_region_mae_dn'] <= 8))
    common.write_json(work/'quality.json', quality)
    # Full resolution center and distributed crops; index values are never averaged.
    w,h=min(768,rgb.RasterXSize),min(768,rgb.RasterYSize)
    x,y=max(0,(rgb.RasterXSize-w)//2),max(0,(rgb.RasterYSize-h)//2)
    crop=rgb.ReadAsArray(x,y,w,h)[:3].transpose(1,2,0)
    mapped=colors[map_palette(crop,palette,x,y,cfg.get('dither_strength',4))]
    Image.fromarray(np.concatenate([crop,mapped],axis=1)).save(work/'comparison.png')
    preview_paths=[str(work/'comparison.png')]
    for i,(fx,fy) in enumerate(((.15,.15),(.85,.15),(.15,.85),(.85,.85))):
        w,h=min(512,rgb.RasterXSize),min(512,rgb.RasterYSize)
        x,y=int((rgb.RasterXSize-w)*fx),int((rgb.RasterYSize-h)*fy)
        data=rgb.ReadAsArray(x,y,w,h)
        if not np.any(data[3]): continue
        crop=data[:3].transpose(1,2,0)
        mapped=colors[map_palette(crop,palette,x,y,cfg.get('dither_strength',4))]
        preview=work/f'comparison_{i+1}.png'
        Image.fromarray(np.concatenate([crop,mapped],axis=1)).save(preview)
        preview_paths.append(str(preview))
    rgb = None
    if not quality['passed']:
        raise RuntimeError(f'调色板误差未通过：MAE={mae:.2f}, P99={p99}；保留 RGB 和候选文件，禁止上传')
    os.replace(partial,out)
    return {'path':str(out),'sha256':common.digest_file(out),'size':out.stat().st_size,
            'epsg':epsg,'resolution_m':[5,5],'color':'palette8','quality':quality,
            'comparison':str(work/'comparison.png'),'comparisons':preview_paths,'reference_rgb':str(rgb_path)}
