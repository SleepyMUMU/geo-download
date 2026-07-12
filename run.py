# -*- coding: utf-8 -*-
"""
卫星影像工作流一键运行入口 (高精度版)
=================================
功能：
  - 快速配置目标城市，无需手动修改底层的多个 YAML 配置文件
  - 使用高精度市级 Shapefile（C:/satellite-pipeline/city_detailed/3. City/city.shp）
  - 智能匹配城市名称（自动识别带“市”后缀的规范名称，如将“威海”匹配为“威海市”）
  - 动态生成临时配置，运行下载模块（download_tiles.py）与处理模块（process_tiles.py）
  - 完美支持控制台实时进度展示与多线程加速

用法：
  conda activate sat
  python run.py 威海
"""

import os
import sys
import yaml
import subprocess
import geopandas as gpd

# 使用 SCRIPT_DIR 动态获取路径，确保项目迁移后可移植
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_CITY_SHP = os.path.join(SCRIPT_DIR, "city_detailed/3. City/city_wgs84.shp")
BASE_CONFIG_DOWNLOAD = os.path.join(SCRIPT_DIR, "config.yaml")
BASE_CONFIG_PROCESS = os.path.join(SCRIPT_DIR, "config_process.yaml")

TEMP_CONFIG_DOWNLOAD = os.path.join(SCRIPT_DIR, "_temp_config.yaml")
TEMP_CONFIG_PROCESS = os.path.join(SCRIPT_DIR, "_temp_config_process.yaml")


def load_yaml(filepath):
    if not os.path.exists(filepath):
        print(f"[ERROR] 未找到基础配置文件: {filepath}")
        sys.exit(1)
    with open(filepath, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def save_yaml(filepath, data):
    with open(filepath, "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, allow_unicode=True)


def find_exact_name(shp_path, query_name):
    """
    在高精度市级 Shapefile 中智能检索城市的准确名称（基于 ct_name 字段）。
    支持输入"威海"匹配"威海市"，或输入"武汉市"匹配"武汉"。
    """
    print(f"[INFO] 正在高精度 Shapefile 中检索城市 '{query_name}'...")
    try:
        # 只读取属性表以加快速度（忽略 geometry）
        # pyogrio 引擎支持 ignore_geometry=True
        gdf = gpd.read_file(shp_path, ignore_geometry=True)
    except Exception as e:
        print(f"[ERROR] 无法读取高精度边界数据: {e}")
        sys.exit(1)

    # 1. 尝试精确匹配
    match = gdf[gdf["ct_name"] == query_name]
    if not match.empty:
        return query_name

    # 2. 去除“市/区/县”后缀进行模糊匹配
    clean_query = query_name.rstrip("市区县")
    mask = gdf["ct_name"].astype(str).str.contains(clean_query, na=False) | gdf["ct_name"].astype(str).str.startswith(clean_query, na=False)
    matches = gdf[mask]

    if not matches.empty:
        exact_name = matches["ct_name"].values[0]
        print(f"[INFO] 匹配成功: 输入 '{query_name}' -> 匹配到高精度数据中的标准名称 '{exact_name}'")
        return exact_name

    # 3. 匹配失败，列出前几个可用值供参考
    print(f"[ERROR] 未在边界数据中找到与 '{query_name}' 匹配的城市！")
    print(f"        数据中可用的城市名称示例 (前15个):")
    unique_names = list(gdf["ct_name"].dropna().unique()[:15])
    print(f"        {unique_names}")
    sys.exit(1)


def main():
    # 1. 解析目标城市
    if len(sys.argv) > 1:
        target_city = sys.argv[1].strip()
    else:
        target_city = input("请输入您要下载的目标城市名称 (例如: 威海, 深圳, 武汉): ").strip()

    if not target_city:
        print("[ERROR] 城市名称不能为空！")
        sys.exit(1)

    # 2. 检查高精度边界文件是否存在
    if not os.path.exists(DEFAULT_CITY_SHP):
        print(f"[ERROR] 高精度市级 Shapefile 文件不存在: {DEFAULT_CITY_SHP}")
        print("        请确认 3. City.zip 已成功解压。")
        sys.exit(1)

    # 3. 获取高精度边界数据中的标准名称
    exact_city_name = find_exact_name(DEFAULT_CITY_SHP, target_city)

    print("=" * 60)
    print(f" 开始执行高精度卫星影像一键流水线 | 标准目标区域: {exact_city_name}")
    print("=" * 60)

    # 4. 动态配置下载模块 (_temp_config.yaml)
    dl_config = load_yaml(BASE_CONFIG_DOWNLOAD)
    dl_config["shp_path"] = DEFAULT_CITY_SHP
    dl_config["shp_filter_column"] = "ct_name"
    dl_config["shp_filter_value"] = exact_city_name
    save_yaml(TEMP_CONFIG_DOWNLOAD, dl_config)

    # 5. 动态配置处理模块 (_temp_config_process.yaml)
    proc_config = load_yaml(BASE_CONFIG_PROCESS)
    proc_config["shp_path"] = DEFAULT_CITY_SHP
    proc_config["shp_filter_column"] = "ct_name"
    proc_config["shp_filter_value"] = exact_city_name
    save_yaml(TEMP_CONFIG_PROCESS, proc_config)

    # 6. 执行第一阶段：下载
    print("\n>>> 启动第一阶段: 瓦片智能下载...")
    try:
        result_dl = subprocess.run(
            [sys.executable, "-u", "C:/satellite-pipeline/download_tiles.py", "--config", TEMP_CONFIG_DOWNLOAD],
            check=True
        )
    except subprocess.CalledProcessError as e:
        print(f"\n[ERROR] 下载阶段出错中断: {e}")
        cleanup_temp()
        sys.exit(1)

    # 7. 执行第三阶段：拼合压制处理
    print("\n>>> 启动第三阶段: 影像拼合与极限压制...")
    try:
        result_proc = subprocess.run(
            [sys.executable, "-u", "C:/satellite-pipeline/process_tiles.py", "--config", TEMP_CONFIG_PROCESS],
            check=True
        )
    except subprocess.CalledProcessError as e:
        print(f"\n[ERROR] 处理阶段出错中断: {e}")
        cleanup_temp()
        sys.exit(1)

    # 8. 清理临时配置文件
    cleanup_temp()

    print("\n" + "=" * 60)
    print(f" 高精度一键流水线执行完毕!")
    print(f" 最终成果已生成至: {proc_config.get('output_dir', 'C:/satellite-pipeline/output')}/final_compressed.tif")
    print("=" * 60)


def cleanup_temp():
    """清理运行中产生的临时 YAML 配置文件"""
    for temp_file in [TEMP_CONFIG_DOWNLOAD, TEMP_CONFIG_PROCESS]:
        if os.path.exists(temp_file):
            try:
                os.remove(temp_file)
            except OSError:
                pass


if __name__ == "__main__":
    main()
