"""Retired unsafe entrypoint; use explicit v2 job manifests."""
import sys
print('旧入口已停用。使用 python pipeline.py --help；先生成精确任务清单，再执行 run/review/upload。', file=sys.stderr)
sys.exit(2)
