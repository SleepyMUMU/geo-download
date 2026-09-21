# -*- coding: utf-8 -*-
"""
卫星影像工作流省份一键批量运行入口 (异步流水线版)
=============================================
功能：
  - 采用生产者-消费者并发模型，下载与压制并行化
  - 自动输出至本地日志文件，避免控制台冲突交织
  - 压制成功后自动异步调用 `upload_to_quark.py` 触发云端备份
"""
import os
import sys
import io
import yaml
import subprocess
import geopandas as gpd
import threading
from queue import Queue
from concurrent.futures import ThreadPoolExecutor, Future
from osgeo import gdal

gdal.UseExceptions()

# 修复 Windows GBK 控制台编码问题并启用 ANSI 虚拟终端颜色支持
if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace', line_buffering=True)
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace', line_buffering=True)
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        kernel32.SetConsoleMode(kernel32.GetStdHandle(-11), 7)
    except:
        pass

# ANSI 终端颜色定义
COLOR_CYAN = "\033[96m"
COLOR_GREEN = "\033[92m"
COLOR_YELLOW = "\033[93m"
COLOR_RED = "\033[91m"
COLOR_RESET = "\033[0m"

# 使用 SCRIPT_DIR 动态获取路径，确保项目迁移后可移植
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_PROVINCE_SHP = os.path.join(SCRIPT_DIR, "sheng_detailed/sheng_wgs84.shp")
DEFAULT_CITY_SHP = os.path.join(SCRIPT_DIR, "city_detailed/3. City/city_wgs84.shp")
BASE_CONFIG_DOWNLOAD = os.path.join(SCRIPT_DIR, "config.yaml")
BASE_CONFIG_PROCESS = os.path.join(SCRIPT_DIR, "config_process.yaml")

LOGS_DIR = os.path.join(SCRIPT_DIR, "logs")
os.makedirs(LOGS_DIR, exist_ok=True)

def load_yaml(filepath):
    if not os.path.exists(filepath):
        print(f"[ERROR] 未找到基础配置文件: {filepath}")
        sys.exit(1)
    with open(filepath, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)

def save_yaml(filepath, data):
    with open(filepath, "w", encoding="utf-8") as f:
        yaml.safe_dump(data, f, allow_unicode=True)

# 线程安全的控制台打印锁
print_lock = threading.Lock()

def safe_print(msg):
    with print_lock:
        print(msg)

def run_command_stream(cmd, log_path, tag, tag_color):
    """
    运行外部命令，将 stdout/stderr 实时同时输出至终端控制台（带自定义标记前缀和颜色）并持久化写入对应的日志文件
    """
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    with open(log_path, "w", encoding="utf-8", buffering=1) as log_file:
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace"
        )
        
        def stream_reader(pipe, is_stderr):
            try:
                for line in iter(pipe.readline, ''):
                    if not line:
                        break
                    clean_line = line.rstrip('\r\n')
                    # 写入对应的历史日志文件
                    prefix_file = "[STDERR] " if is_stderr else ""
                    log_file.write(f"{prefix_file}{line}")
                    log_file.flush()
                    
                    # 实时格式化输出到控制台
                    col = COLOR_RED if is_stderr else tag_color
                    lbl = f"{tag}:ERR" if is_stderr else tag
                    safe_print(f"{col}[{lbl}]{COLOR_RESET} {clean_line}")
            except Exception:
                pass
            finally:
                pipe.close()
                
        t_out = threading.Thread(target=stream_reader, args=(process.stdout, False))
        t_err = threading.Thread(target=stream_reader, args=(process.stderr, True))
        t_out.start()
        t_err.start()
        
        t_out.join()
        t_err.join()
        return process.wait()

def download_worker(cities, queue):
    """
    下载线程 (生产者): 按顺序执行各城市的下载
    """
    for idx, city in enumerate(cities, 1):
        # 移除可能破坏路径的特殊字符
        safe_city_name = city.replace("/", "_").replace("\\", "_")
        temp_config_path = os.path.join(SCRIPT_DIR, f"_temp_config_download_{safe_city_name}.yaml")
        log_path = f"{LOGS_DIR}/{safe_city_name}_download.log"
        
        safe_print(f"[PRODUCER] >>> [{city}] ({idx}/{len(cities)}) 开始背景下载瓦片... 详情查看: {log_path}")
        
        try:
            # 动态写入临时下载配置
            dl_config = load_yaml(BASE_CONFIG_DOWNLOAD)
            dl_config["shp_path"] = DEFAULT_CITY_SHP
            dl_config["shp_filter_column"] = "ct_name"
            dl_config["shp_filter_value"] = city
            save_yaml(temp_config_path, dl_config)
            
            # 运行下载模块并实时重定向/流式输出日志
            returncode = run_command_stream(
                [sys.executable, "-u", os.path.join(SCRIPT_DIR, "download_tiles.py"), "--config", temp_config_path],
                log_path,
                f"DOWNLOAD:{city}",
                COLOR_CYAN
            )
            
            # 清理临时下载配置
            if os.path.exists(temp_config_path):
                os.remove(temp_config_path)
                
            if returncode == 0:
                safe_print(f"[PRODUCER] === [{city}] 瓦片下载完毕，已推送至压制处理队列！")
                queue.put((city, True))
            else:
                safe_print(f"[PRODUCER] [ERROR] === [{city}] 下载失败！报错详情请查看: {log_path}")
                queue.put((city, False))
                
        except Exception as e:
            safe_print(f"[PRODUCER] [ERROR] === [{city}] 下载线程遭遇未捕获异常: {e}")
            if os.path.exists(temp_config_path):
                try: os.remove(temp_config_path)
                except: pass
            queue.put((city, False))
            
    # 下载结束哨兵
    queue.put((None, None))

def main():
    # 1. 解析目标省份
    if len(sys.argv) > 1:
        province_name = sys.argv[1].strip()
    else:
        province_name = input("请输入目标省份名称 (例如: 辽宁): ").strip()

    if not province_name:
        print("[ERROR] 省份名称不能为空！")
        sys.exit(1)

    clean_prov = province_name.rstrip("省")

    # 2. 检查高精度边界文件是否存在
    if not os.path.exists(DEFAULT_CITY_SHP):
        print(f"[ERROR] 高精度市级 Shapefile 文件不存在: {DEFAULT_CITY_SHP}")
        sys.exit(1)

    # 3. 加载边界数据提取城市列表
    print(f"[INFO] 正在检索属于 '{clean_prov}' 的所有地级城市...")
    try:
        gdf = gpd.read_file(DEFAULT_CITY_SHP, ignore_geometry=True)
    except Exception as e:
        print(f"[ERROR] 无法读取 Shapefile 数据: {e}")
        sys.exit(1)

    # 查找匹配的省份要素
    prov_mask = gdf["pr_name"].astype(str).str.contains(clean_prov, na=False)
    matched_gdf = gdf[prov_mask]

    if matched_gdf.empty:
        print(f"[ERROR] 未能在数据库中匹配到省份: {province_name}")
        sys.exit(1)

    # 获取该省份下的所有城市标准名称
    all_cities = sorted(matched_gdf["ct_name"].dropna().unique())
    
    # 过滤掉已经生成过最终 TIFF 成果的城市，避免重复工作
    cities = []
    skipped_cities = []
    for city in all_cities:
        target_tif = os.path.join(SCRIPT_DIR, "output", f"{city}.tif")
        if os.path.exists(target_tif):
            skipped_cities.append(city)
        else:
            cities.append(city)
            
    print("=" * 60)
    print(f" 异步双引擎流水线就绪 | 省份: {clean_prov} | 城市总量: {len(all_cities)} | 待处理: {len(cities)}")
    if skipped_cities:
        print(f" 已存在并跳过的城市: {', '.join(skipped_cities)}")
    print(" 计划处理城市列表:")
    for c in cities:
        print(f"  - {c}")
    print("=" * 60)

    if not cities and not skipped_cities:
        print("[INFO] 所有地级城市均已拥有成果且无上传任务，无需运行！")
        sys.exit(0)

    # 4. 启动下载后台线程
    processing_queue = Queue()
    downloader_thread = None
    if cities:
        downloader_thread = threading.Thread(target=download_worker, args=(cities, processing_queue), name="Downloader")
        downloader_thread.start()
    else:
        # 没有要下载的城市，直接推入下载结束哨兵
        processing_queue.put((None, None))

    # 5. 主线程消费队列进行压制 (上传异步化)
    success_count = 0
    fail_cities = []
    upload_futures = []  # 收集异步上传 Future
    
    completed_downloads = 0
    total_cities = len(cities)
    
    # 上传线程池 (最多 1 个并发上传)
    upload_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="Uploader")
    
    def async_upload(city_name):
        """异步上传单个城市到夸克网盘"""
        safe_name = city_name.replace("/", "_").replace("\\", "_")
        upload_log_path = f"{LOGS_DIR}/{safe_name}_upload.log"
        try:
            returncode = run_command_stream(
                [sys.executable, os.path.join(SCRIPT_DIR, "upload_to_quark.py"), "--city", city_name],
                upload_log_path,
                f"UPLOAD:{city_name}",
                COLOR_YELLOW
            )
            if returncode == 0:
                safe_print(f"[UPLOADER] [SUCCESS] === [{city_name}] 夸克网盘备份完毕!")
                return True
            else:
                safe_print(f"[UPLOADER] [ERROR] === [{city_name}] 上传失败! 详情: {upload_log_path}")
                return False
        except Exception as e:
            safe_print(f"[UPLOADER] [ERROR] === [{city_name}] 上传异常: {e}")
            return False
            
    # 自动把本地已存在、但可能云端未上传成功的城市提交补传
    if skipped_cities:
        safe_print(f"[INFO] 正在为已跳过且本地已存在成果的城市提交网盘自动补传任务: {', '.join(skipped_cities)}")
        for city in skipped_cities:
            future = upload_pool.submit(async_upload, city)
            upload_futures.append((city, future))
    
    while completed_downloads < total_cities:
        city, download_success = processing_queue.get()
        if city is None:  # 哨兵信号，代表下载线程完成
            break
            
        completed_downloads += 1
        safe_city_name = city.replace("/", "_").replace("\\", "_")
        
        if not download_success:
            safe_print(f"[CONSUMER] [SKIP] === [{city}] 由于下载失败，跳过后续压制裁剪。")
            fail_cities.append(city)
            processing_queue.task_done()
            continue
            
        # 开始拼合、重采样、裁剪与压缩
        temp_config_path = os.path.join(SCRIPT_DIR, f"_temp_config_process_{safe_city_name}.yaml")
        log_path = f"{LOGS_DIR}/{safe_city_name}_process.log"
        safe_print(f"[CONSUMER] >>> [{city}] 开始重采样、裁剪与无损压制... 详情查看: {log_path}")
        
        try:
            proc_config = load_yaml(BASE_CONFIG_PROCESS)
            proc_config["shp_path"] = DEFAULT_CITY_SHP
            proc_config["shp_filter_column"] = "ct_name"
            proc_config["shp_filter_value"] = city
            save_yaml(temp_config_path, proc_config)
            
            returncode = run_command_stream(
                [sys.executable, "-u", os.path.join(SCRIPT_DIR, "process_tiles.py"), "--config", temp_config_path],
                log_path,
                f"PROCESS:{city}",
                COLOR_GREEN
            )
                
            # 清理临时文件
            if os.path.exists(temp_config_path):
                os.remove(temp_config_path)
                
            if returncode == 0:
                safe_print(f"[CONSUMER] [SUCCESS] === [{city}] 重采样裁剪与压缩完成！")
                success_count += 1
                
                # 异步提交上传任务 (fire-and-forget，不阻塞后续压制)
                safe_print(f"[CONSUMER] [ASYNC-UPLOAD] >>> [{city}] 已提交至上传线程池，压制继续推进...")
                future = upload_pool.submit(async_upload, city)
                upload_futures.append((city, future))
            else:
                safe_print(f"[CONSUMER] [ERROR] === [{city}] 压制阶段出错，跳过该城市！日志详情: {log_path}")
                fail_cities.append(city)
                
        except Exception as e:
            safe_print(f"[CONSUMER] [ERROR] === [{city}] 压制线程遭遇异常: {e}")
            if os.path.exists(temp_config_path):
                try: os.remove(temp_config_path)
                except: pass
            fail_cities.append(city)
            
        processing_queue.task_done()

    if downloader_thread:
        downloader_thread.join()
    
    # 6. 等待所有异步上传完成
    safe_print("\n[INFO] 所有城市压制完毕，等待异步上传任务全部结束...")
    upload_pool.shutdown(wait=True)
    
    upload_success = 0
    upload_fail = []
    for city_name, future in upload_futures:
        try:
            if future.result():
                upload_success += 1
            else:
                upload_fail.append(city_name)
        except Exception as e:
            upload_fail.append(city_name)
    
    print("\n" + "=" * 60)
    print(f" 异步三引擎批量流水线执行完毕!")
    print(f" 压制成功: {success_count}/{len(cities)} 个城市")
    print(f" 上传成功: {upload_success}/{len(upload_futures)} 个城市")
    if fail_cities:
        print(f" 压制失败城市: {fail_cities}")
    if upload_fail:
        print(f" 上传失败城市: {upload_fail}")
    print("=" * 60)

if __name__ == "__main__":
    main()
