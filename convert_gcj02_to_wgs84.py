import os
import math
import argparse
import geopandas as gpd
from shapely.ops import transform

def out_of_china(lng, lat):
    return not (72.004 <= lng <= 137.8347 and 0.8293 <= lat <= 55.8271)

def transform_lat(lng, lat):
    ret = -100.0 + 2.0 * lng + 3.0 * lat + 0.2 * lat * lat + 0.1 * lng * lat + 0.2 * math.sqrt(abs(lng))
    ret += (20.0 * math.sin(6.0 * lng * math.pi) + 20.0 * math.sin(2.0 * lng * math.pi)) * 2.0 / 3.0
    ret += (20.0 * math.sin(lat * math.pi) + 40.0 * math.sin(lat / 3.0 * math.pi)) * 2.0 / 3.0
    ret += (160.0 * math.sin(lat / 12.0 * math.pi) + 320 * math.sin(lat * math.pi / 30.0)) * 2.0 / 3.0
    return ret

def transform_lng(lng, lat):
    ret = 300.0 + lng + 2.0 * lat + 0.1 * lng * lng + 0.1 * lng * lat + 0.1 * math.sqrt(abs(lng))
    ret += (20.0 * math.sin(6.0 * lng * math.pi) + 20.0 * math.sin(2.0 * lng * math.pi)) * 2.0 / 3.0
    ret += (20.0 * math.sin(lng * math.pi) + 40.0 * math.sin(lng / 3.0 * math.pi)) * 2.0 / 3.0
    ret += (150.0 * math.sin(lng / 12.0 * math.pi) + 300.0 * math.sin(lng / 30.0 * math.pi)) * 2.0 / 3.0
    return ret

def wgs84_to_gcj02(lng, lat):
    if out_of_china(lng, lat):
        return lng, lat
    d_lat = transform_lat(lng - 105.0, lat - 35.0)
    d_lng = transform_lng(lng - 105.0, lat - 35.0)
    rad_lat = lat / 180.0 * math.pi
    magic = math.sin(rad_lat)
    magic = 1 - 0.00669342162296594323 * magic * magic
    sqrt_magic = math.sqrt(magic)
    d_lat = (d_lat * 180.0) / ((6378245.0 * (1 - 0.00669342162296594323)) / (magic * sqrt_magic) * math.pi)
    d_lng = (d_lng * 180.0) / (6378245.0 / sqrt_magic * math.cos(rad_lat) * math.pi)
    return lng + d_lng, lat + d_lat

def gcj02_to_wgs84_exact(gcj_lng, gcj_lat):
    if out_of_china(gcj_lng, gcj_lat):
        return gcj_lng, gcj_lat
    
    init_lng, init_lat = gcj_lng, gcj_lat
    wgs_lng, wgs_lat = init_lng, init_lat
    
    # 迭代逼近法
    for i in range(10):
        c_lng, c_lat = wgs84_to_gcj02(wgs_lng, wgs_lat)
        d_lng = c_lng - gcj_lng
        d_lat = c_lat - gcj_lat
        if abs(d_lng) < 1e-8 and abs(d_lat) < 1e-8:
            break
        wgs_lng -= d_lng
        wgs_lat -= d_lat
        
    return wgs_lng, wgs_lat

def transform_point(x, y, z=None):
    wgs_x, wgs_y = gcj02_to_wgs84_exact(x, y)
    if z is not None:
        return wgs_x, wgs_y, z
    return wgs_x, wgs_y

def convert_geometry(geom):
    if geom is None:
        return None
    return transform(transform_point, geom)

def process_shapefile(input_shp, output_shp):
    print(f"[*] 加载 Shapefile: {input_shp}")
    gdf = gpd.read_file(input_shp)
    
    print(f"[*] 开始进行 GCJ-02 到 WGS84 的高精度纠偏... (共 {len(gdf)} 个要素)")
    
    gdf['geometry'] = gdf['geometry'].apply(convert_geometry)
    
    print(f"[*] 纠偏完成，正在保存到: {output_shp}")
    # 设置CRS为WGS84
    gdf.crs = "EPSG:4326"
    gdf.to_file(output_shp, encoding='utf-8')
    print("[*] 保存成功！")
    
    # 对比一下前一个要素的边界变化
    if len(gdf) > 0:
        old_gdf = gpd.read_file(input_shp)
        old_bounds = old_gdf.iloc[0].geometry.bounds
        new_bounds = gdf.iloc[0].geometry.bounds
        print(f"\n[验证] 示例边界框变化:")
        print(f"  原(GCJ-02): {old_bounds}")
        print(f"  新(WGS84):  {new_bounds}")
        diff_x = new_bounds[0] - old_bounds[0]
        diff_y = new_bounds[1] - old_bounds[1]
        print(f"  变化量: Lng {diff_x:.6f}, Lat {diff_y:.6f}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("path", help="输入文件或目录")
    args = parser.parse_args()
    
    if not os.path.exists(args.path):
        print(f"错误: 找不到 {args.path}")
        exit(1)
        
    if os.path.isdir(args.path):
        for root, dirs, files in os.walk(args.path):
            for file in files:
                if file.endswith(".shp") and not file.endswith("_wgs84.shp"):
                    input_shp = os.path.join(root, file)
                    output_shp = os.path.join(root, file.replace(".shp", "_wgs84.shp"))
                    process_shapefile(input_shp, output_shp)
    else:
        if args.path.endswith(".shp"):
            output_shp = args.path.replace(".shp", "_wgs84.shp")
            process_shapefile(args.path, output_shp)
