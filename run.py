"""Compatibility entrypoint: named city or v2 subcommands."""
import sys
import geo_common as common
from tasks import make_plan
from pipeline import main, execute
if __name__ == '__main__':
    try:
        if len(sys.argv)==2 and sys.argv[1] not in ('-h','--help'):
            cfg=common.load_config()
            path,_=make_plan(cfg,city_names=[sys.argv[1]])
            print(f'任务文件: {path}')
            execute(path,cfg,'run')
        else:
            main()
    except Exception as exc:
        print(f'[失败] {exc}',file=sys.stderr)
        sys.exit(1)
