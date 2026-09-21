"""Shared, fail-closed configuration, geometry and artifact handling."""
import hashlib
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
# Conda executables invoked without activation still need their own GDAL/PROJ data.
for key, suffix in [('GDAL_DATA', 'Library/share/gdal'), ('PROJ_DATA', 'Library/share/proj')]:
    candidate = Path(sys.prefix) / suffix
    if candidate.exists():
        os.environ.setdefault(key, str(candidate))
if sys.platform == 'win32':
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8', errors='replace')

import yaml
from osgeo import gdal
gdal.UseExceptions()

def digest_file(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()

def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode()).hexdigest()

def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + '.partial')
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(tmp, path)

def load_config(path=None):
    path = Path(path or ROOT / 'config.yaml').resolve()
    cfg = yaml.safe_load(path.read_text(encoding='utf-8'))
    for key in ('city_shp', 'cache_dir', 'output_dir', 'jobs_dir'):
        value = Path(cfg[key])
        cfg[key] = str(value if value.is_absolute() else path.parent / value)
    if cfg['resolution'] != 5 or cfg['color_mode'] != 'palette8':
        raise ValueError('本项目验收规格为 5 m、GM 8-bit 调色板；更改规格须重新验收')
    if cfg.get('source_crs') != 'EPSG:3857' or cfg.get('tile_size') != 256:
        raise ValueError('当前仅支持 WGS84/Web Mercator XYZ、256 像素瓦片')
    if cfg.get('tile_source') != 'custom' or not all(s in cfg['custom_url'] for s in ('{x}', '{y}', '{z}')):
        raise ValueError('需要明确的 XYZ 图源 URL')
    return cfg

def cities(cfg):
    import geopandas as gpd
    df = gpd.read_file(cfg['city_shp'])
    if df.crs is None:
        raise ValueError('行政边界缺少 CRS，禁止猜测或自动 GCJ02 纠偏')
    for col in ('ct_name', 'pr_name', 'ct_adcode'):
        if col not in df:
            raise ValueError(f'行政边界缺少字段 {col}')
    return df.to_crs(4326)

def match_city(df, name, province=None):
    query = df if province is None else df[df.pr_name.isin([province, province + '省'])]
    hits = query[query.ct_name == name]
    if hits.empty:
        hits = query[query.ct_name.str.removesuffix('市') == name.removesuffix('市')]
    codes = hits.ct_adcode.astype(str).unique()
    if len(codes) != 1:
        raise ValueError(f'城市名称无法唯一匹配: {name!r}; 候选={hits.ct_name.tolist()}')
    return hits.iloc[0]

def geometry_for(df, code):
    from shapely.ops import unary_union
    geom = unary_union(df[df.ct_adcode.astype(str) == str(code)].geometry)
    if geom.is_empty or not geom.is_valid or geom.geom_type not in ('Polygon', 'MultiPolygon'):
        raise ValueError('边界为空、无效或不是面，需修复输入后再运行')
    return geom

def utm_for(geom):
    lon, lat = geom.centroid.x, geom.centroid.y
    if not -80 <= lat <= 84:
        raise ValueError('区域超出 UTM 覆盖纬度')
    zone = min(60, max(1, int((lon + 180) // 6) + 1))
    return (32600 if lat >= 0 else 32700) + zone

def source_id(cfg):
    return fingerprint({k: cfg[k] for k in ('custom_url', 'source_crs', 'tile_size', 'zoom')})[:20]

def tile_path(cfg, tile):
    return Path(cfg['cache_dir']) / source_id(cfg) / str(tile.z) / str(tile.x) / f'{tile.y}.png'

def expected_tiles(geom, zoom):
    import mercantile
    from shapely.geometry import box
    from shapely.prepared import prep
    boundary = prep(geom)
    return [t for t in mercantile.tiles(*geom.bounds, zooms=zoom)
            if boundary.intersects(box(*mercantile.bounds(t)))]

def boundary_hash(df, code):
    return hashlib.sha256(geometry_for(df, code).wkb).hexdigest()

def job_config_hash(cfg):
    return fingerprint({k: v for k, v in cfg.items() if k not in
                        ('proxy', 'max_threads', 'retries', 'quark', 'cache_dir', 'output_dir', 'jobs_dir', 'city_shp')})
