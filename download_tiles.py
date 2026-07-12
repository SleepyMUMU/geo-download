# -*- coding: utf-8 -*-
"""
卫星瓦片智能下载器
===================
功能：
  - 根据 SHP 边界或矩形 Bounding Box 计算所需瓦片
  - 并发下载瓦片（支持多图源：Esri, Google, Bing）
  - 断点续传（跳过已下载的瓦片）
  - 代理支持（适配国内网络环境）
  - 下载完成后输出统计报告

用法：
  conda activate sat
  python download_tiles.py --config config.yaml
"""

import argparse
import os
import sys
import io

# 修复 Windows GBK 控制台编码问题
if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace', line_buffering=True)
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace', line_buffering=True)
import time
import math
import yaml
import requests
import mercantile
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from shapely.geometry import box, shape
from shapely.ops import unary_union
from osgeo import gdal
gdal.UseExceptions()

# ============================================================
# 图源 URL 模板
# ============================================================
TILE_SOURCES = {
    "esri": "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
    "google": "https://mt{s}.google.com/vt/lyrs=s&x={x}&y={y}&z={z}",
    "bing": "https://ecn.t{s}.tiles.virtualearth.net/tiles/a{quadkey}.jpeg?g=587",
}

# Google/Bing 子域名轮询
SUBDOMAINS = ["0", "1", "2", "3"]


def load_config(config_path: str) -> dict:
    """加载 YAML 配置文件"""
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def get_tile_url(source: str, custom_url: str, x: int, y: int, z: int) -> str:
    """根据图源生成瓦片 URL"""
    if source == "custom":
        return custom_url.format(x=x, y=y, z=z)

    if source == "bing":
        quadkey = mercantile.quadkey(x, y, z)
        s = SUBDOMAINS[hash(quadkey) % len(SUBDOMAINS)]
        return TILE_SOURCES["bing"].format(s=s, quadkey=quadkey)

    if source == "google":
        s = SUBDOMAINS[(x + y) % len(SUBDOMAINS)]
        return TILE_SOURCES["google"].format(s=s, x=x, y=y, z=z)

    # esri (默认)
    return TILE_SOURCES.get(source, TILE_SOURCES["esri"]).format(x=x, y=y, z=z)


def load_boundary(config: dict):
    """
    加载下载边界。
    优先使用 SHP 文件，否则使用 bbox 矩形。
    支持通过属性字段过滤特定的市/县。
    返回 shapely 几何体。
    """
    shp_path = config.get("shp_path", "")
    if shp_path and os.path.exists(shp_path):
        import geopandas as gpd
        gdf = gpd.read_file(shp_path)
        
        # 属性过滤：选择特定城市
        filter_col = config.get("shp_filter_column", "")
        filter_val = config.get("shp_filter_value", "")
        if filter_col and filter_val:
            # 确保列存在
            if filter_col in gdf.columns:
                gdf_filtered = gdf[gdf[filter_col] == filter_val]
                if gdf_filtered.empty:
                    print(f"[WARNING] 过滤条件 {filter_col} == '{filter_val}' 未匹配到任何要素，将使用全部边界！")
                    print(f"          可用的字段值示例: {gdf[filter_col].dropna().unique()[:10]}")
                else:
                    gdf = gdf_filtered
                    print(f"[INFO] 已过滤 SHP 边界: {filter_col} == '{filter_val}' ({len(gdf)} 个要素)")
            else:
                print(f"[WARNING] SHP 文件中不存在过滤列 '{filter_col}'！可用列有: {list(gdf.columns)}")
        
        # 确保使用 WGS84 (EPSG:4326)
        if gdf.crs and gdf.crs.to_epsg() != 4326:
            gdf = gdf.to_crs(epsg=4326)
        boundary = unary_union(gdf.geometry)
        print(f"[INFO] 已加载 SHP 边界: {shp_path}")
        print(f"       总边界框: {boundary.bounds}")
        return boundary
    else:
        bbox = config.get("bbox", [-122.42, 37.77, -122.40, 37.79])
        boundary = box(*bbox)
        print(f"[INFO] 使用矩形边界: {bbox}")
        return boundary


def calculate_tiles(boundary, zoom: int) -> list:
    """
    计算与边界多边形相交的所有瓦片。
    返回 mercantile.Tile 列表。
    """
    # 获取外接矩形内所有候选瓦片
    bounds = boundary.bounds  # (minx, miny, maxx, maxy)
    all_tiles = list(mercantile.tiles(bounds[0], bounds[1], bounds[2], bounds[3], zooms=zoom))

    # 精确筛选：只保留与多边形实际相交的瓦片
    intersecting_tiles = []
    for tile in all_tiles:
        tile_bounds = mercantile.bounds(tile)
        tile_box = box(tile_bounds.west, tile_bounds.south, tile_bounds.east, tile_bounds.north)
        if boundary.intersects(tile_box):
            intersecting_tiles.append(tile)

    skipped = len(all_tiles) - len(intersecting_tiles)
    print(f"[INFO] Zoom {zoom}: 外接矩形瓦片 {len(all_tiles)} 块, "
          f"实际相交 {len(intersecting_tiles)} 块 (跳过 {skipped} 块无关瓦片)")

    return intersecting_tiles


def download_single_tile(tile, config: dict, session: requests.Session) -> dict:
    """
    下载单个瓦片。
    返回结果字典: {"tile": tile, "status": "ok"|"skip"|"error", "msg": "..."}
    """
    x, y, z = tile.x, tile.y, tile.z
    source = config.get("tile_source", "esri")
    custom_url = config.get("custom_url", "")
    output_dir = config.get("output_dir", "tiles")
    tile_format = config.get("tile_format", "png")
    skip_existing = config.get("skip_existing", True)

    # 构建输出路径: output_dir/{z}/{x}/{y}.png
    tile_dir = os.path.join(output_dir, str(z), str(x))
    tile_path = os.path.join(tile_dir, f"{y}.{tile_format}")

    # 断点续传
    if skip_existing and os.path.exists(tile_path):
        file_size = os.path.getsize(tile_path)
        if file_size > 0:
            return {"tile": tile, "status": "skip", "msg": "已存在，跳过"}

    # 构建 URL
    url = get_tile_url(source, custom_url, x, y, z)

    try:
        response = session.get(url, timeout=30)
        if response.status_code == 200:
            os.makedirs(tile_dir, exist_ok=True)
            with open(tile_path, "wb") as f:
                f.write(response.content)
            return {"tile": tile, "status": "ok", "msg": f"{len(response.content)} bytes"}
        else:
            return {"tile": tile, "status": "error", "msg": f"HTTP {response.status_code}"}
    except requests.exceptions.RequestException as e:
        return {"tile": tile, "status": "error", "msg": str(e)[:100]}


def run_download(config: dict):
    """执行下载主流程"""
    print("=" * 60)
    print("  卫星瓦片智能下载器 v1.0")
    print("=" * 60)

    # 1. 加载边界
    boundary = load_boundary(config)

    # 2. 计算瓦片列表
    zoom = config.get("zoom", 16)
    tiles = calculate_tiles(boundary, zoom)

    if not tiles:
        print("[ERROR] 没有找到需要下载的瓦片，请检查边界配置。")
        return

    # 3. 配置网络会话
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "image/webp,image/apng,image/*,*/*;q=0.8",
    })

    # 代理配置
    proxy = config.get("proxy", "")
    if proxy:
        session.proxies = {"http": proxy, "https": proxy}
        print(f"[INFO] 使用代理: {proxy}")

    # 4. 并发下载
    max_threads = config.get("max_threads", 8)
    print(f"[INFO] 开始下载: {len(tiles)} 块瓦片, {max_threads} 线程并发")
    print(f"[INFO] 图源: {config.get('tile_source', 'esri')}")
    print(f"[INFO] 输出: {config.get('output_dir', 'tiles')}")
    print("-" * 60)

    stats = {"ok": 0, "skip": 0, "error": 0, "errors": []}
    start_time = time.time()

    with ThreadPoolExecutor(max_workers=max_threads) as executor:
        futures = {
            executor.submit(download_single_tile, tile, config, session): tile
            for tile in tiles
        }

        for i, future in enumerate(as_completed(futures), 1):
            result = future.result()
            stats[result["status"]] += 1

            if result["status"] == "error":
                stats["errors"].append(result)

            # 进度报告：每 1% 或最后一块打印一次进度
            report_interval = max(1, len(tiles) // 100)
            if i % report_interval == 0 or i == len(tiles):
                elapsed = time.time() - start_time
                speed = i / elapsed if elapsed > 0 else 0
                pct = i / len(tiles) * 100
                print(f"  [{pct:5.1f}%] {i}/{len(tiles)} "
                      f"| OK {stats['ok']} | SKIP {stats['skip']} "
                      f"| FAIL {stats['error']} | {speed:.1f} tiles/s")

    # 5. 输出统计报告
    total_time = time.time() - start_time
    print("\n" + "=" * 60)
    print("  下载完成 - 统计报告")
    print("=" * 60)
    print(f"  总瓦片数:  {len(tiles)}")
    print(f"  新下载:    {stats['ok']}")
    print(f"  已跳过:    {stats['skip']}")
    print(f"  失败:      {stats['error']}")
    print(f"  总耗时:    {total_time:.1f} 秒")
    if stats['ok'] > 0:
        print(f"  下载速度:  {stats['ok'] / total_time:.1f} 块/秒")

    if stats["errors"]:
        print(f"\n  [WARNING] {len(stats['errors'])} 块瓦片下载失败:")
        for err in stats["errors"][:10]:
            t = err["tile"]
            print(f"    z={t.z} x={t.x} y={t.y} → {err['msg']}")
        if len(stats["errors"]) > 10:
            print(f"    ... 还有 {len(stats['errors']) - 10} 个错误")

    print("=" * 60)


def main():
    parser = argparse.ArgumentParser(description="卫星瓦片智能下载器")
    parser.add_argument("--config", default="config.yaml", help="配置文件路径")
    args = parser.parse_args()

    if not os.path.exists(args.config):
        print(f"[ERROR] 配置文件不存在: {args.config}")
        sys.exit(1)

    config = load_config(args.config)
    run_download(config)


if __name__ == "__main__":
    main()
