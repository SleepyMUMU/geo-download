# -*- coding: utf-8 -*-
"""
卫星影像拼合与极限压制工具
=======================
功能：
  1. 为本地 XYZ 瓦片生成 *.pgw 坐标世界文件（进行 Web Mercator EPSG:3857 地理定位）
  2. 使用 gdalbuildvrt 虚拟拼合所有瓦片，不占用内存
  3. 使用 gdalwarp 进行 Lanczos/Cubic 重采样至 5 米分辨率，若提供 SHP 则进行精确裁剪
  4. 使用 rgb2pct 将 24-bit RGB 降维为 8-bit PCT (索引色)
  5. 使用 gdal_translate 进行最高比例无损压缩 (DEFLATE, ZLEVEL=9, PREDICTOR=1)
  6. 为最终 GeoTIFF 建立金字塔 (Pyramids)，保证大文件秒开

用法：
  conda activate sat
  python process_tiles.py --config config_process.yaml
"""

import argparse
import os
import glob
import sys
import io
import yaml
import mercantile
from pathlib import Path

# 修复 Windows GBK 控制台编码问题
if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace', line_buffering=True)
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace', line_buffering=True)

from osgeo import gdal
gdal.UseExceptions()
gdal.PushErrorHandler('CPLQuietErrorHandler')
from osgeo_utils import rgb2pct


def load_config(config_path: str) -> dict:
    """加载 YAML 配置文件"""
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def generate_world_files(tile_files: list, tile_format: str = "png"):
    """
    为给定的瓦片文件列表生成世界文件进行地理定位 (EPSG:3857)
    """
    print("[INFO] 开始生成瓦片坐标世界文件...")
    if not tile_files:
        print("[ERROR] 瓦片文件列表为空！")
        return False
        
    wld_ext = "pgw" if tile_format.lower() == "png" else "jgw"
    created_count = 0
    skipped_count = 0
    
    for file_path in tile_files:
        # 获取 x, y, z
        parts = Path(file_path).parts
        try:
            y_name = parts[-1]
            x_name = parts[-2]
            z_name = parts[-3]
            
            y = int(y_name.split(".")[0])
            x = int(x_name)
            z = int(z_name)
        except (ValueError, IndexError):
            # 兼容普通反斜杠路径分割
            norm_path = os.path.normpath(file_path)
            parts = norm_path.split(os.sep)
            y = int(parts[-1].split(".")[0])
            x = int(parts[-2])
            z = int(parts[-3])
            
        wld_path = os.path.splitext(file_path)[0] + f".{wld_ext}"
        
        if os.path.exists(wld_path):
            skipped_count += 1
            continue
            
        # 计算 tile 边界 (Web Mercator EPSG:3857)
        tile = mercantile.Tile(x=x, y=y, z=z)
        left, bottom, right, top = mercantile.xy_bounds(tile)
        
        # 瓦片默认为 256x256 像素
        # 计算世界文件 6 行参数
        A = (right - left) / 256.0
        D = 0.0
        B = 0.0
        E = (bottom - top) / 256.0
        C = left + A / 2.0
        F = top + E / 2.0
        
        with open(wld_path, "w") as wld_f:
            wld_f.write(f"{A:.10f}\n{D:.10f}\n{B:.10f}\n{E:.10f}\n{C:.10f}\n{F:.10f}\n")
        
        created_count += 1

    print(f"[INFO] 坐标生成完毕: 新建 {created_count} 个世界文件，跳过 {skipped_count} 个已存在的文件。")
    return True


def run_processing(config: dict):
    print("=" * 60)
    print("  卫星影像拼合与极限压制处理器 v1.0")
    print("=" * 60)
    
    tiles_dir = config.get("tiles_dir", "tiles")
    zoom = config.get("zoom", 16)
    tile_format = config.get("tile_format", "png")
    shp_path = config.get("shp_path", "")
    output_dir = config.get("output_dir", "output")
    resampling_method = config.get("resampling_method", "cubic")
    resolution = config.get("resolution", 5.0)  # 默认 5m 分辨率
    
    os.makedirs(output_dir, exist_ok=True)
    
    # 临时文件和最终文件路径
    filter_col = config.get("shp_filter_column", "")
    filter_val = config.get("shp_filter_value", "")
    prefix = filter_val if filter_val else "temp"
    
    vrt_path = os.path.join(output_dir, f"{prefix}_mosaic.vrt")
    resampled_rgb_path = os.path.join(output_dir, f"{prefix}_resampled_rgb.tif")
    paletted_tmp_path = os.path.join(output_dir, f"{prefix}_paletted_tmp.tif")
    
    # 动态使用城市名称命名输出成果
    output_filename = f"{filter_val}.tif" if filter_val else "final_compressed.tif"
    final_output_path = os.path.join(output_dir, output_filename)
    
    # 1. 精准提取本次处理需要拼合的瓦片列表 (基于城市的 SHP 空间范围做过滤)
    print("[INFO] Step 1: 精准检索和筛选属于当前城市的瓦片...")
    filtered_tiles = []
    use_spatial_filter = False
    
    if shp_path and os.path.exists(shp_path) and filter_col and filter_val:
        try:
            import geopandas as gpd
            from shapely.ops import unary_union
            from shapely.geometry import box
            
            print(f"[INFO] 正在计算 '{filter_val}' 的空间边界并筛选对应瓦片...")
            gdf = gpd.read_file(shp_path)
            gdf_filtered = gdf[gdf[filter_col] == filter_val]
            if not gdf_filtered.empty:
                if gdf_filtered.crs and gdf_filtered.crs.to_epsg() != 4326:
                    gdf_filtered = gdf_filtered.to_crs(epsg=4326)
                boundary = unary_union(gdf_filtered.geometry)
                
                # 计算相交瓦片 (与 download_tiles.py 逻辑相同)
                bounds = boundary.bounds  # (minx, miny, maxx, maxy)
                all_tiles = list(mercantile.tiles(bounds[0], bounds[1], bounds[2], bounds[3], zooms=zoom))
                
                intersecting_coords = set()
                for tile in all_tiles:
                    tile_bounds = mercantile.bounds(tile)
                    tile_box = box(tile_bounds.west, tile_bounds.south, tile_bounds.east, tile_bounds.north)
                    if boundary.intersects(tile_box):
                        intersecting_coords.add((tile.x, tile.y))
                
                # 遍历这些相交瓦片，如果本地存在，则加入输入列表
                for x, y in intersecting_coords:
                    tile_path = os.path.join(tiles_dir, str(zoom), str(x), f"{y}.{tile_format}")
                    if os.path.exists(tile_path):
                        filtered_tiles.append(tile_path)
                
                use_spatial_filter = True
                print(f"[INFO] 空间边界筛选完成。相交瓦片数: {len(intersecting_coords)}, 本地存在瓦片数: {len(filtered_tiles)}")
        except Exception as e:
            print(f"[WARNING] 空间边界瓦片筛选失败: {e}，将回退到全量扫描。")
            
    if not use_spatial_filter:
        search_pattern = os.path.join(tiles_dir, str(zoom), "*", f"*.{tile_format}")
        input_tiles = glob.glob(search_pattern)
    else:
        input_tiles = filtered_tiles
        
    if not input_tiles:
        print("[ERROR] 没有找到输入瓦片文件用于拼合。")
        return
        
    # 2. 生成 pgw / jgw 世界定位文件 (仅限本次过滤出的瓦片，由 73万次 -> 5万次，速度提升十倍以上)
    if not generate_world_files(input_tiles, tile_format):
        return
        
    # 3. 构建 VRT 虚拟拼合
    print("[INFO] Step 2: 建立 VRT 虚拟拼合...")
    # 使用 gdal.BuildVRT
    vrt_options = gdal.BuildVRTOptions(outputSRS="EPSG:3857", addAlpha=True)
    vrt_ds = gdal.BuildVRT(vrt_path, input_tiles, options=vrt_options)
    vrt_ds = None  # 显式关闭并释放，确保 VRT 文件刷盘写入
    print(f"[INFO] VRT 建立成功: {vrt_path}")
    
    # 3. 重采样 & 裁剪 (gdalwarp)
    target_srs = config.get("output_srs", "EPSG:3857")
    is_geographic = any(code in target_srs for code in ["4326", "4490", "4214"])
    
    if is_geographic:
        # 将分辨率从米(m)转换为度(degree) (1度约等于 111319.9 米)
        deg_res = resolution / 111319.9
        x_res = deg_res
        y_res = deg_res
        print(f"[INFO] Step 2: 目标坐标系为地理坐标系 ({target_srs})，分辨率已从 {resolution}m 转换为 {deg_res:.8f}度")
    else:
        x_res = resolution
        y_res = resolution
        print(f"[INFO] Step 2: 重采样至 {resolution}m ({resampling_method})，目标坐标系: {target_srs} ...")

    warp_options_dict = {
        "format": "GTiff",
        "xRes": x_res,
        "yRes": y_res,
        "resampleAlg": resampling_method,
        "dstSRS": target_srs,
        "dstAlpha": True,
        "warpOptions": ["NUM_THREADS=ALL_CPUS"],
    }
    
    # 若有 SHP 边界，则加入裁剪参数
    if shp_path and os.path.exists(shp_path):
        print(f"[INFO] 检测到 SHP 边界，启用精准裁剪: {shp_path}")
        warp_options_dict["cutlineDSName"] = shp_path
        warp_options_dict["cropToCutline"] = True
        
        # 属性过滤：仅用该属性裁剪
        filter_col = config.get("shp_filter_column", "")
        filter_val = config.get("shp_filter_value", "")
        if filter_col and filter_val:
            warp_options_dict["cutlineWhere"] = f"{filter_col} = '{filter_val}'"
            print(f"[INFO] 裁剪过滤条件已添加: {filter_col} = '{filter_val}'")
        
    warp_options = gdal.WarpOptions(**warp_options_dict)
    warp_ds = gdal.Warp(resampled_rgb_path, vrt_path, options=warp_options, callback=gdal.TermProgress)
    warp_ds = None  # 显式释放
    print(f"[INFO] 重采样及裁剪完成: {resampled_rgb_path}")
    
    # 4. 无损压制导出 (gdal_translate) — 直接压缩 24-bit RGB，不做 PCT 量化
    print("[INFO] Step 3: 无损压制导出最终 GeoTIFF (24-bit RGB + DEFLATE + PREDICTOR=2)...")
    translate_options = gdal.TranslateOptions(
        format="GTiff",
        creationOptions=[
            "COMPRESS=DEFLATE",
            "ZLEVEL=6",
            "PREDICTOR=2",  # 水平差分预测，对连续 RGB 影像压缩效率极高
            "BIGTIFF=YES",
            "TILED=YES",
            "NUM_THREADS=ALL_CPUS"
        ]
    )
    translate_ds = gdal.Translate(final_output_path, resampled_rgb_path, options=translate_options, callback=gdal.TermProgress)
    translate_ds = None  # 显式释放
    print(f"[INFO] 无损压缩 GeoTIFF 导出完成: {final_output_path}")
    
    # 5. 建立金字塔 (gdaladdo)
    print("[INFO] Step 4: 为最终 GeoTIFF 建立金字塔...")
    final_ds = gdal.Open(final_output_path, gdal.GA_Update)
    if final_ds is not None:
        final_ds.BuildOverviews('NEAREST', [2, 4, 8, 16, 32])
        final_ds = None
        print("[INFO] 金字塔建立成功！")
    else:
        print("[WARNING] 无法打开最终文件建立金字塔。")
        
    # 6. 清理临时文件
    print("[INFO] 正在清理临时文件...")
    for temp_file in [vrt_path, resampled_rgb_path]:
        if os.path.exists(temp_file):
            try:
                os.remove(temp_file)
            except OSError as e:
                print(f"[WARNING] 无法删除临时文件 {temp_file}: {e}")
                
    print("\n" + "=" * 60)
    print("  全流程处理完成！")
    print(f"  最终成果已生成至: {final_output_path}")
    print("=" * 60)


def main():
    parser = argparse.ArgumentParser(description="卫星影像拼合与极限压制处理器")
    parser.add_argument("--config", default="config_process.yaml", help="配置文件路径")
    args = parser.parse_args()

    if not os.path.exists(args.config):
        print(f"[ERROR] 配置文件不存在: {args.config}")
        sys.exit(1)

    config = load_config(args.config)
    run_processing(config)


if __name__ == "__main__":
    main()
