"""按用户提供 ZIP 中的原始 Shapefile 文件选择新疆下载任务。"""
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

# 按原始 ZIP 文件计数：16～30 所在的10个文件，再追加其后的5个文件。
DEFAULT_SOURCE_FILES = (
    '16.shp', '17.shp', '18.shp', '19_23.shp', '20_21.shp',
    '22_24.shp', '25_26_28.shp', '102_27.shp', '29_101.shp', '30.shp',
    '31.shp', '32.shp', '33.shp', '34_35.shp', '36_37.shp',
)
SOURCE_FILE_CODES = {
    '16.shp': ('650521',),
    '17.shp': ('650522',),
    '18.shp': ('652301',),
    '19_23.shp': ('652302', '652327'),
    '20_21.shp': ('652323', '652324'),
    '22_24.shp': ('652325', '652328'),
    '25_26_28.shp': ('652701', '652702', '652723'),
    '102_27.shp': ('652722', '659007'),
    '29_101.shp': ('652801', '659006'),
    '30.shp': ('652822',),
    '31.shp': ('652823',),
    '32.shp': ('652824',),
    '33.shp': ('652825',),
    '34_35.shp': ('652826', '652827'),
    '36_37.shp': ('652828', '652829'),
}
EXCLUDED_CODES = {'659006', '659007'}  # 铁门关市、双河市

def select_source_files(g, source_files):
    selected=[]
    for source_file in source_files:
        if source_file not in SOURCE_FILE_CODES:
            raise ValueError(f'未知原始文件: {source_file}')
        for code in SOURCE_FILE_CODES[source_file]:
            if code in EXCLUDED_CODES:
                continue
            hit=g[g.dt_adcode==code]
            if len(hit)!=1:
                raise ValueError(f'{source_file} 对应行政区不存在或不唯一: {code}')
            selected.append((source_file,hit.iloc[0]))
    return selected

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
        fields=['文件序号','源文件','城市','code','下载状态','压制状态','上传状态','last_update','备注']
        tmp=self.folder/'status.csv.partial'
        with tmp.open('w',encoding='utf-8-sig',newline='') as f:
            w=csv.DictWriter(f,fieldnames=fields); w.writeheader(); w.writerows(rows)
        tmp.replace(self.folder/'status.csv')
        lines=['| 文件序号 | 源文件 | 城市 | 下载状态 | 压制状态 | 上传状态 | 更新时间 | 备注 |','|---:|---|---|---|---|---|---|---|']
        lines += [f"| {r['文件序号']} | {r['源文件']} | {r['城市']} | {r['下载状态']} | {r['压制状态']} | {r['上传状态']} | {r['last_update']} | {r['备注']} |" for r in rows]
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
    p=argparse.ArgumentParser()
    p.add_argument('--source-file',action='append',choices=SOURCE_FILE_CODES)
    p.add_argument('--downloaders',type=int,default=2)
    p.add_argument('--session-id',required=True)
    p.add_argument('--session-input-file',required=True)
    a=p.parse_args(); cfg=common.load_config(); g=catalog().reset_index(drop=True)
    source_files=tuple(a.source_file or DEFAULT_SOURCE_FILES)
    chosen=select_source_files(g,source_files)
    batch=Path(cfg['jobs_dir'])/'xinjiang-files-16-through-36_37'
    now=time.strftime('%Y-%m-%d %H:%M:%S')
    file_numbers={name:i+1 for i,name in enumerate(source_files)}
    rows=[{'文件序号':file_numbers[source_file],'源文件':source_file,'城市':r.dt_name,'code':str(r.dt_adcode),
           '下载状态':'等待','压制状态':'等待','上传状态':'等待','last_update':now,'备注':''}
          for source_file,r in chosen]
    status=Status(batch,rows); completed=queue.Queue(); upload_pool=ThreadPoolExecutor(max_workers=1); upload_futures=[]
    qclient=Quark(cfg,Path(a.session_input_file).read_text(encoding='utf-8'),a.session_id)

    def upload_one(job,artifact,work):
        code=job['code']; status.update(code,上传状态='上传中')
        try:
            receipt=qclient.upload(artifact['path'],work/'upload_receipt.json')
            status.update(code,上传状态='已上传并核验',备注=receipt.get('verification',''))
        except Exception as exc:
            status.update(code,上传状态='失败',备注=str(exc).replace('|','/')[:180]); raise

    def download_one(job,geom,work):
        code=job['code']; state=work/'state.json'; artifact=valid_existing(state)
        if artifact:
            status.update(code,下载状态='已下载',压制状态='已压制',备注='复用已校验成果')
            completed.put((job,geom,work,None,artifact,None)); return
        status.update(code,下载状态='下载中')
        try:
            tiles=download(cfg,geom); status.update(code,下载状态='已下载')
            completed.put((job,geom,work,tiles,None,None))
        except Exception as exc:
            status.update(code,下载状态='失败',压制状态='跳过',上传状态='跳过',备注=str(exc).replace('|','/')[:180])
            completed.put((job,geom,work,None,None,exc))

    with lock(cfg):
        with ThreadPoolExecutor(max_workers=a.downloaders) as pool:
            for source_file,r in chosen:
                job={'source_file':source_file,'name':r.dt_name,'code':str(r.dt_adcode),'epsg':common.utm_for(r.geometry)}
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