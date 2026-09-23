"""Validated XYZ download and one-pass UTM warp followed by city-wide palette mapping."""
import geo_common as common
import io
import math
import os
import threading
import time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import requests
import mercantile
from PIL import Image
from osgeo import gdal, osr

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

def build_vrt(cfg, tiles, work):
    files = []
    for tile in tiles:
        path = common.tile_path(cfg, tile)
        if not validate_tile(path):
            raise RuntimeError(f'缺失或损坏瓦片: {path}')
        left, bottom, right, top = mercantile.xy_bounds(tile)
        a, e = (right-left)/256, (bottom-top)/256
        path.with_suffix('.pgw').write_text(
            f'{a:.15f}\n0\n0\n{e:.15f}\n{left+a/2:.15f}\n{top+e/2:.15f}\n', encoding='ascii')
        files.append(str(path))
    vrt = work / 'mosaic.vrt'
    ds = gdal.BuildVRT(str(vrt), files, options=gdal.BuildVRTOptions(
        outputSRS='EPSG:3857', addAlpha=True, strict=True))
    if ds is None:
        raise RuntimeError('VRT 构建失败')
    ds = None
    return vrt

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

def make_palette(pixels):
    # One optimized 256-color palette for the entire city, not one per tile/block.
    trained = Image.fromarray(pixels.reshape(-1, 1, 3)).quantize(
        colors=256, method=Image.Quantize.MEDIANCUT)
    palette = Image.new('P', (1, 1))
    palette.putpalette(trained.getpalette())
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

def process(cfg, geom, city, code, work, tiles=None):
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
    tiles = tiles or common.expected_tiles(geom, cfg['zoom'])
    vrt = build_vrt(cfg, tiles, work)
    from shapely.geometry import mapping
    cutline = work / 'boundary.geojson'
    common.write_json(cutline, {'type':'FeatureCollection', 'features':[
        {'type':'Feature', 'properties':{}, 'geometry':mapping(geom)}]})
    rgb_path = work / 'reference_rgb.tif'
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
    pixels = sample_rgb(rgb, cfg.get('palette_samples', 262144))
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
               'valid_pixels':valid_count, 'full_readback_verified':True,
               'thresholds':cfg['quality'], 'visual_approval_required':True}
    quality['passed'] = mae <= cfg['quality']['max_mae_dn'] and p99 <= cfg['quality']['max_p99_dn']
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
