"""Archive selection is explicit; archive contents are data, never instructions."""
import geo_common as common
import json
import re
import zipfile
from pathlib import Path, PurePosixPath

def zip_entries(archive):
    with zipfile.ZipFile(archive) as z:
        names = {}
        for entry in z.infolist():
            name = entry.filename.replace('\\', '/')
            p = PurePosixPath(name)
            if p.is_absolute() or '..' in p.parts or ':' in name or entry.flag_bits & 1:
                raise ValueError(f'压缩包含不安全/加密路径: {name}')
            if name in names:
                raise ValueError(f'压缩包含重名条目: {name}')
            names[name] = entry
        return names

def resolve_file(archive, selected, cfg, df, scratch):
    names = zip_entries(archive)
    if selected not in names or names[selected].is_dir():
        raise ValueError(f'未找到指定文件的精确路径: {selected}')
    tokens = re.split(r'[\s/\\_\-().（）\[\]【】]+', selected)
    candidates = {}
    for _, row in df.iterrows():
        city = row.ct_name
        aliases = {city, city.removesuffix('市'), str(row.ct_adcode)}
        if aliases.intersection(tokens):
            candidates[str(row.ct_adcode)] = row
    provinces = set(df.pr_name) | {p.removesuffix('省') for p in df.pr_name}
    selected_provinces = provinces.intersection(tokens)
    if selected_provinces:
        candidates = {code: row for code,row in candidates.items()
                      if row.pr_name in selected_provinces or row.pr_name.removesuffix('省') in selected_provinces}
    evidence = {'filename_candidates':list(candidates)}
    suffix = PurePosixPath(selected).suffix.lower()
    if suffix in ('.shp','.geojson','.gpkg','.kml','.tif','.tiff'):
        import geopandas as gpd
        stem = str(PurePosixPath(selected).with_suffix(''))
        wanted = [selected]
        if suffix == '.shp':
            wanted = [n for n in names if str(PurePosixPath(n).with_suffix('')) == stem and
                      PurePosixPath(n).suffix.lower() in ('.shp','.shx','.dbf','.prj','.cpg')]
            if not {'.shp','.shx','.dbf','.prj'}.issubset({PurePosixPath(n).suffix.lower() for n in wanted}):
                raise ValueError('选中的 SHP 缺少 .shx/.dbf/.prj 配套文件')
        if sum(names[n].file_size for n in wanted) > 512*1024**2:
            raise ValueError('选中矢量文件超过 512 MiB，需单独检查')
        folder = Path(scratch)/common.fingerprint(selected)[:12]
        folder.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(archive) as z:
            for n in wanted:
                (folder/PurePosixPath(n).name).write_bytes(z.read(names[n]))
        target=folder/PurePosixPath(selected).name
        if suffix in ('.tif','.tiff'):
            from osgeo import gdal
            from shapely.geometry import Polygon
            ds=gdal.Open(str(target))
            if not ds or not ds.GetProjection(): raise ValueError('影像没有投影信息')
            gt=ds.GetGeoTransform()
            corners=[gdal.ApplyGeoTransform(gt,x,y) for x,y in
                     ((0,0),(ds.RasterXSize,0),(ds.RasterXSize,ds.RasterYSize),(0,ds.RasterYSize))]
            vector=gpd.GeoDataFrame(geometry=[Polygon(corners)],crs=ds.GetProjection())
            ds=None
        else:
            if suffix=='.gpkg' and len(gpd.list_layers(target))!=1:
                raise ValueError('GeoPackage 含多图层，需要先指定/导出目标图层')
            vector = gpd.read_file(target)
        attr_codes=set()
        for field in ('ct_name','city','city_name','城市','市名'):
            if field in vector:
                for value in vector[field].dropna().unique():
                    attr_codes.add(str(common.match_city(df,str(value)).ct_adcode))
        for field in ('ct_adcode','city_code'):
            if field in vector:
                attr_codes.update(str(v) for v in vector[field].dropna().unique())
        if len(attr_codes)>1:
            raise ValueError('选中文件的城市属性包含多个城市，需要拆分或澄清')
        if vector.empty or vector.crs is None:
            raise ValueError('选中文件为空或缺少 CRS，无法根据位置判定城市')
        if not vector.geometry.is_valid.all():
            raise ValueError('选中文件含无效几何')
        from shapely.ops import unary_union
        geometry = unary_union(vector.to_crs(4326).geometry)
        # Require >=99% of the selected footprint inside one city. Never silently
        # choose the first/largest city for a genuinely multi-city input.
        local_crs = common.utm_for(geometry)
        v = gpd.GeoSeries([geometry],crs=4326).to_crs(local_crs).iloc[0]
        nearby = df[df.intersects(geometry)].to_crs(local_crs)
        matches = {}
        for code, group in nearby.groupby('ct_adcode'):
            city_geom = unary_union(group.geometry)
            ratio = v.intersection(city_geom).area/v.area if v.area else float(city_geom.covers(v))
            if ratio >= .99:
                matches[str(code)] = ratio
        evidence['spatial_coverage'] = matches
        if len(matches) != 1:
            raise ValueError(f'位置不能唯一归属一个城市（可能跨市）: {selected}')
        code = next(iter(matches))
        if candidates and code not in candidates:
            raise ValueError('文件名与文件地理位置冲突，需澄清')
        if attr_codes and code not in attr_codes:
            raise ValueError('城市属性与文件地理位置冲突，需澄清')
        evidence['attribute_candidates']=list(attr_codes)
        candidates = {code:df[df.ct_adcode.astype(str)==code].iloc[0]}
    if len(candidates) != 1:
        raise ValueError(f'不能唯一判定城市: {selected}; 候选={list(candidates)}；请提供明确文件名、城市属性或边界')
    row = next(iter(candidates.values()))
    return row, evidence

def make_plan(cfg, city_names=None, archive=None, selected=None, province=None):
    df = common.cities(cfg)
    rows = []
    inputs = []
    if archive:
        archive = str(Path(archive).resolve())
        if not selected:
            raise ValueError('必须逐个 --select 指定 ZIP 内文件；不允许默认处理全包')
        for name in selected:
            name = name.replace('\\','/')
            row, evidence = resolve_file(archive,name,cfg,df,Path(cfg['jobs_dir'])/'inspection')
            rows.append(row)
            inputs.append({'archive':archive,'archive_sha256':common.digest_file(archive),
                           'selected_file':name,'city_code':str(row.ct_adcode),'evidence':evidence})
    elif city_names:
        rows = [common.match_city(df,name,province) for name in city_names]
        inputs = [{'explicit_city':r.ct_name,'city_code':str(r.ct_adcode)} for r in rows]
    else:
        raise ValueError('需要 ZIP 及指定文件或明确城市')
    jobs = []
    seen = set()
    for row in rows:
        code = str(row.ct_adcode)
        if code in seen:
            continue
        seen.add(code)
        geom = common.geometry_for(df,code)
        jobs.append({'city':row.ct_name,'province':row.pr_name,'code':code,
                     'boundary_sha256':common.boundary_hash(df,code),'epsg':common.utm_for(geom),
                     'expected_tiles':len(common.expected_tiles(geom,cfg['zoom']))})
    plan = {'schema':2,'scope':'full_city','inputs':inputs,'jobs':jobs,
            'config_hash':common.job_config_hash(cfg),'source_url':cfg['custom_url'],
            'zoom':cfg['zoom'],'resolution_m':[5,5],'color_mode':'palette8',
            'resampling':'average','boundary_provenance':'bundled *_wgs84; geographic accuracy not independently verified'}
    plan['plan_id'] = common.fingerprint(plan)[:20]
    dest = Path(cfg['jobs_dir'])/plan['plan_id']/'plan.json'
    common.write_json(dest,plan)
    return dest,plan

def validate_plan(path,cfg):
    plan = json.loads(Path(path).read_text(encoding='utf-8'))
    body = {k:v for k,v in plan.items() if k!='plan_id'}
    if common.fingerprint(body)[:20] != plan['plan_id'] or plan['config_hash'] != common.job_config_hash(cfg):
        raise ValueError('任务或参数已改变，请重新生成计划')
    for item in plan['inputs']:
        if 'archive' in item and common.digest_file(item['archive']) != item['archive_sha256']:
            raise ValueError('原始压缩包发生变化，请重新识别')
    df = common.cities(cfg)
    for job in plan['jobs']:
        if common.boundary_hash(df,job['code']) != job['boundary_sha256']:
            raise ValueError('行政边界已改变，请重新生成计划')
    return plan,df
