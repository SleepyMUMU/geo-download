"""Bounded live verification; never starts a full city download."""
import geo_common as common
import argparse
import json
import os
import secrets
import time
from pathlib import Path

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('mode',choices=['imagery','cloud-info','cloud-upload','cloud-login'])
    parser.add_argument('--session-input-file')
    args=parser.parse_args()
    cfg=common.load_config()
    folder=common.ROOT/'validation'/'private'
    folder.mkdir(parents=True,exist_ok=True)
    if args.mode=='imagery':
        from shapely.geometry import box
        from imagery import download,process
        # Weihai: city/coast sample, approximately 2.2 by 1.8 km.
        geom=box(122.10,37.49,122.125,37.51)
        cfg.update(cache_dir=str(folder/'tiles'),jobs_dir=str(folder/'jobs'),output_dir=str(folder/'output'))
        start=time.monotonic()
        tiles=download(cfg,geom)
        result=process(cfg,geom,'威海小样','sample',folder/'sample',tiles)
        result['elapsed_seconds']=time.monotonic()-start
        result['tile_count']=len(tiles)
        common.write_json(folder/'sample_result.json',result)
        print(json.dumps(result,ensure_ascii=False,indent=2))
    else:
        from quark_backend import Quark
        session=folder/'session.txt'
        if not session.exists(): session.write_text(f'{int(time.time())}-{secrets.token_hex(3)}',encoding='utf-8')
        q=Quark(cfg,Path(args.session_input_file).read_text(encoding='utf-8'),session.read_text())
        if args.mode in ('cloud-info','cloud-login'):
            _,data=q.call(['get-user-info' if args.mode=='cloud-info' else 'login'])
            print('Cloud authentication check succeeded')
        else:
            test=folder/'geo_pipeline_upload_probe_64MiB.bin'
            if not test.exists():
                with test.open('wb') as f:
                    for _ in range(64):f.write(os.urandom(1024**2))
            receipt=q.upload(test,folder/'cloud_probe_receipt.json')
            print(json.dumps(receipt,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
