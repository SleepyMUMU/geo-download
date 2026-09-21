"""第16～30项：双下载生产者、单压制消费者、单上传线程。"""
import argparse
import csv
import json
import queue
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import geo_common as common
from imagery import download, process
from pipeline import lock
from quark_backend import Quark
from xinjiang import catalog

ROOT=Path(__file__).resolve().parent

class Status:
    def __init__(self,folder,rows):
        self.folder=Path(folder); self.folder.mkdir(parents=True,exist_ok=True)
        self.rows={r['code']:r for r in rows}; self.guard=threading.Lock(); self.save()
    def update(self,code,**values):
        with self.guard:
            self.rows[code].update(values,last_update=time.strftime('%Y-%m-%d %H:%M:%S'))
            self.save()
    def save(self):
        rows=list(self.rows.values())
        common.write_json(self.folder/'status.json',rows)
        tmp=self.folder/'status.csv.partial'
        with tmp.open('w',encoding='utf-8-sig',newline='') as f:
            w=csv.DictWriter(f,fieldnames=['序号','城市','code','下载状态','压制状态','上传状态','last_update','备注'])
            w.writeheader(); w.writerows(rows)
        tmp.replace(self.folder/'status.csv')
        lines=['| 序号 | 城市 | 下载状态 | 压制状态 | 上传状态 | 更新时间 | 备注 |','|---:|---|---|---|---|---|---|']
        lines += [f"| {r['序号']} | {r['城市']} | {r['下载状态']} | {r['压制状态']} | {r['上传状态']} | {r['last_update']} | {r['备注']} |" for r in rows]
        md=self.folder/'status.md.partial'; md.write_text('\n'.join(lines)+'\n',encoding='utf-8'); md.replace(self.folder/'status.md')

def valid_existing(state_path):
    if not state_path.exists(): return None
    try:
        state=json.loads(state_path.read_text(encoding='utf-8')); artifact=state.get('artifact')
        if artifact and Path(artifact['path']).exists() and common.digest_file(artifact['path'])==artifact['sha256']:
            return artifact
    except Exception: pass
    return None

def main():
    p=argparse.ArgumentParser(); p.add_argument('--start',type=int,default=16);p.add_argument('--end',type=int,default=30)
    p.add_argument('--downloaders',type=int,default=2);p.add_argument('--session-id',required=True);p.add_argument('--session-input-file',required=True)
    a=p.parse_args(); cfg=common.load_config(); g=catalog().reset_index(drop=True)
    if a.start<1 or a.end>len(g) or a.start>a.end: raise ValueError('选择范围无效')
    chosen=g.iloc[a.start-1:a.end].copy(); batch=Path(cfg['jobs_dir'])/f'xinjiang-batch-{a.start}-{a.end}'
    now=time.strftime('%Y-%m-%d %H:%M:%S')
    rows=[{'序号':i,'城市':r.dt_name,'code':str(r.dt_adcode),'下载状态':'等待','压制状态':'等待','上传状态':'等待',
           'last_update':now,'备注':''} for i,(_,r) in enumerate(chosen.iterrows(),a.start)]
    status=Status(batch,rows); completed=queue.Queue(); upload_pool=ThreadPoolExecutor(max_workers=1); upload_futures=[]
    qclient=Quark(cfg,Path(a.session_input_file).read_text(encoding='utf-8'),a.session_id)

    def upload_one(job,artifact,work):
        code=job['code']; status.update(code,上传状态='上传中')
        try:
            receipt=qclient.upload(artifact['path'],work/'upload_receipt.json')
            status.update(code,上传状态='已上传并核验',备注=receipt.get('verification',''))
        except Exception as exc:
            status.update(code,上传状态='失败',备注=str(exc).replace('|','/')[:180])
            raise

    def download_one(job,geom,work):
        code=job['code']; state=work/'state.json'; artifact=valid_existing(state)
        if artifact:
            status.update(code,下载状态='已下载',压制状态='已压制',备注='复用已校验成果')
            completed.put((job,geom,work,None,artifact,None)); return
        status.update(code,下载状态='下载中')
        try:
            tiles=download(cfg,geom)
            status.update(code,下载状态='已下载')
            completed.put((job,geom,work,tiles,None,None))
        except Exception as exc:
            status.update(code,下载状态='失败',压制状态='跳过',上传状态='跳过',备注=str(exc).replace('|','/')[:180])
            completed.put((job,geom,work,None,None,exc))

    with lock(cfg):
        with ThreadPoolExecutor(max_workers=a.downloaders) as pool:
            for idx,(_,r) in enumerate(chosen.iterrows(),a.start):
                job={'index':idx,'name':r.dt_name,'code':str(r.dt_adcode),'epsg':common.utm_for(r.geometry)}
                work=batch/job['code']; work.mkdir(parents=True,exist_ok=True)
                pool.submit(download_one,job,r.geometry,work)
            for _ in range(len(chosen)):
                job,geom,work,tiles,artifact,error=completed.get(); code=job['code']
                if error: continue
                if artifact is None:
                    status.update(code,压制状态='压制中')
                    try:
                        common.write_json(work/'state.json',{'status':'processing','job':job})
                        artifact=process(cfg,geom,job['name'],code,work,tiles)
                        common.write_json(work/'state.json',{'status':'processed','job':job,'artifact':artifact})
                        status.update(code,压制状态='已压制（质检通过）')
                    except Exception as exc:
                        status.update(code,压制状态='失败',上传状态='跳过',备注=str(exc).replace('|','/')[:180]); continue
                status.update(code,上传状态='排队')
                upload_futures.append(upload_pool.submit(upload_one,job,artifact,work))
        upload_pool.shutdown(wait=True)
    failures=sum(1 for f in upload_futures if f.exception())
    (batch/'COMPLETE.txt').write_text(f'完成时间: {time.strftime("%Y-%m-%d %H:%M:%S")}\n上传失败: {failures}\n',encoding='utf-8')

if __name__=='__main__': main()
