import os
import sys
import io
import subprocess
import glob
import argparse

# 修复 Windows GBK 控制台编码问题
if sys.platform == 'win32':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace', line_buffering=True)
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace', line_buffering=True)

# 城市拼音映射表
CITY_PINYIN_MAP = {
    "丹东": "DanDong",
    "大连": "DaLian",
    "抚顺": "FuShun",
    "朝阳": "ChaoYang",
    "本溪": "BenXi",
    "沈阳": "ShenYang",
    "盘锦": "PanJin",
    "营口": "YingKou",
    "葫芦岛": "HuLuDao",
    "辽阳": "LiaoYang",
    "铁岭": "TieLing",
    "锦州": "JinZhou",
    "阜新": "FuXin",
    "鞍山": "AnShan",
    "威海": "WeiHai"
}

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(SCRIPT_DIR, "output")
REMOTE_BASE_DIR = "/Research/Geo TIFF"
REMOTE_TARGET_DIR = "/Research/Geo TIFF/辽宁"
COOKIE_PATH = r"C:\Users\81052\.gemini\config\skills\kuake_skill\cookie.txt"

def run_cmd(cmd):
    try:
        env = os.environ.copy()
        if os.path.exists(COOKIE_PATH):
            with open(COOKIE_PATH, "r", encoding="utf-8") as f:
                cookie = f.read().strip()
                env["KUAKE_COOKIE"] = cookie
        result = subprocess.run(cmd, shell=True, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace", env=env)
        return True, result.stdout
    except subprocess.CalledProcessError as e:
        return False, e.stderr

def check_remote_dir_exists(remote_dir):
    """检测云端目录是否存在"""
    success, output = run_cmd(f'kuake list "{remote_dir}"')
    if success:
        try:
            import json
            data = json.loads(output)
            return data.get("success") is True
        except Exception:
            return True
    return False

def main():
    parser = argparse.ArgumentParser(description="夸克网盘城市 GeoTIFF 自动上传工具")
    parser.add_argument("--city", default="", help="指定上传的城市名称 (如: 丹东市 或 丹东)")
    args = parser.parse_args()

    print(">>> 开始执行夸克云盘上传任务...")
    
    # 1. 确保目标文件夹存在
    if not check_remote_dir_exists(REMOTE_TARGET_DIR):
        print(f"正在创建云端目录: {REMOTE_TARGET_DIR}")
        run_cmd(f'kuake create "辽宁" "{REMOTE_BASE_DIR}"')
    else:
        print(f"云端目录已存在，跳过创建: {REMOTE_TARGET_DIR}")
    
    # 2. 遍历/筛选本地 output 目录下的 TIF
    tif_files = glob.glob(os.path.join(OUTPUT_DIR, "*.tif"))
    
    # 过滤城市名称（如果指定了 --city）
    target_city = args.city.replace("市", "") if args.city else ""
    
    uploaded_any = False
    for tif_path in tif_files:
        filename = os.path.basename(tif_path)
        if filename == "final_compressed.tif":
            continue
            
        city_name_with_suffix = filename.replace(".tif", "")
        # 去掉“市”字
        city_name = city_name_with_suffix.replace("市", "")
        
        # 如果指定了目标城市，且不匹配，则跳过
        if target_city and city_name != target_city:
            continue
            
        if city_name in CITY_PINYIN_MAP:
            pinyin_name = CITY_PINYIN_MAP[city_name]
            remote_filename = f"{pinyin_name}.tif"
            remote_path = f"{REMOTE_TARGET_DIR}/{remote_filename}"
            
            print(f"\n准备上传: {filename} -> {remote_path}")
            cmd = f'kuake upload "{tif_path}" "{remote_path}"'
            success, output = run_cmd(cmd)
            
            if success:
                print(f"[成功] {remote_filename} 上传完毕!")
                uploaded_any = True
            else:
                print(f"[失败] 上传报错: {output.strip()}")
        else:
            print(f"\n[跳过] 未知城市: {filename}")

    if not uploaded_any and target_city:
        print(f"\n[WARNING] 未找到匹配城市 '{target_city}' 的本地 GeoTIFF 文件用于上传！")

if __name__ == "__main__":
    main()
