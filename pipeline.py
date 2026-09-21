"""Explicit plan -> download -> process -> visual review -> verified upload."""
import geo_common as common
import argparse
import json
import os
import sys
from pathlib import Path
from contextlib import contextmanager
from tasks import make_plan, validate_plan, zip_entries
from imagery import download, process, validate_output

@contextmanager
def lock(cfg):
    path=Path(cfg['jobs_dir'])/'pipeline.lock'
    path.parent.mkdir(parents=True,exist_ok=True)
    try:
        fd=os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY)
    except FileExistsError:
        raise RuntimeError(f'存在运行锁 {path}。确认没有运行中进程后人工移走旧锁。')
    try:
        os.write(fd,str(os.getpid()).encode())
        os.close(fd)
        yield
    finally:
        path.unlink(missing_ok=True)

def execute(plan_path,cfg,action,review_note=None,session_input=None,session_id=None):
    plan,df=validate_plan(plan_path,cfg)
    failures=[]
    with lock(cfg):
        for job in plan['jobs']:
            work=Path(cfg['jobs_dir'])/plan['plan_id']/job['code']
            state_path=work/'state.json'
            state=json.loads(state_path.read_text(encoding='utf-8')) if state_path.exists() else {}
            try:
                artifact=state.get('artifact')
                valid=bool(artifact and Path(artifact['path']).exists()
                           and common.digest_file(artifact['path'])==artifact['sha256'])
                if action=='run':
                    if valid:
                        print(f"已校验现有成果: {job['city']}")
                        continue
                    geom=common.geometry_for(df,job['code'])
                    state={'status':'downloading','job':job}
                    common.write_json(state_path,state)
                    tiles=download(cfg,geom)
                    state['status']='processing'
                    common.write_json(state_path,state)
                    artifact=process(cfg,geom,job['city'],job['code'],work,tiles)
                    state.update(status='awaiting_visual_review',artifact=artifact)
                elif action=='review':
                    if not valid or not review_note:
                        raise RuntimeError('需要完整成果及实际对照图检查记录')
                    validate_output(artifact['path'],job['epsg'])
                    state.update(status='ready_to_upload',visual_review={'note':review_note,'sha256':artifact['sha256']})
                elif action=='upload':
                    if not valid or state.get('visual_review',{}).get('sha256')!=artifact['sha256']:
                        raise RuntimeError('成果未通过目视检查或哈希已变化，禁止上传')
                    from quark_backend import Quark
                    receipt=Quark(cfg,session_input,session_id).upload(artifact['path'],work/'upload_receipt.json')
                    state.update(status='uploaded_verified',upload=receipt)
                state.pop('error',None)
                common.write_json(state_path,state)
            except Exception as exc:
                state.update(status='failed',error=str(exc))
                common.write_json(state_path,state)
                failures.append(job['city']+': '+str(exc))
    if failures:
        raise RuntimeError('\n'.join(failures))

def main():
    parser=argparse.ArgumentParser(description='城市影像：精确选文件、5 m UTM、GM 8-bit、上传核验')
    parser.add_argument('--config',default=str(common.ROOT/'config.yaml'))
    sub=parser.add_subparsers(dest='action',required=True)
    inv=sub.add_parser('inspect'); inv.add_argument('archive')
    p=sub.add_parser('plan')
    p.add_argument('--archive');p.add_argument('--select',action='append')
    p.add_argument('--city',action='append');p.add_argument('--province')
    for name in ('run','review','upload'):
        p=sub.add_parser(name);p.add_argument('plan')
        if name=='review': p.add_argument('--note',required=True)
        if name=='upload':
            p.add_argument('--session-input-file',required=True)
            p.add_argument('--session-id',required=True)
    args=parser.parse_args()
    cfg=common.load_config(args.config)
    if args.action=='inspect':
        print('\n'.join(zip_entries(args.archive)))
    elif args.action=='plan':
        path,plan=make_plan(cfg,args.city,args.archive,args.select,args.province)
        print(json.dumps(plan,ensure_ascii=False,indent=2)); print(f'任务文件: {path}')
    else:
        execute(args.plan,cfg,args.action,getattr(args,'note',None),
                Path(args.session_input_file).read_text(encoding='utf-8') if hasattr(args,'session_input_file') else None,
                getattr(args,'session_id',None))

if __name__=='__main__':
    try:
        main()
    except Exception as exc:
        print(f'[失败] {exc}',file=sys.stderr)
        sys.exit(1)
