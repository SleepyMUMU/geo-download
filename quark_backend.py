"""Official CLI adapter. Success requires NDJSON success and a fresh cloud audit."""
import geo_common as common
import json
import os
import shutil
import subprocess
from pathlib import Path

def parse_result(stdout, returncode):
    events=[]
    for line in stdout.splitlines():
        if line.strip():
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                raise RuntimeError('夸克返回非 JSON 数据，拒绝判定成功')
    results=[e for e in events if e.get('type')=='result']
    failures=[e for e in events if e.get('type') in ('list','result') and e.get('code',0)!=0]
    if returncode or failures or len(results)!=1 or results[0].get('code')!=0:
        messages=[e.get('msg','') for e in failures or results]
        raise RuntimeError('夸克命令未成功: '+'; '.join(messages))
    return events,results[0]['data']

class Quark:
    def __init__(self,cfg,session_input,session_id):
        self.cfg=cfg
        self.options=cfg.get('quark',{})
        self.skill=Path(os.path.expandvars(os.path.expanduser(self.options['skill_dir'])))
        self.session_input=session_input
        self.session_id=session_id
        if not session_input or not session_id:
            raise ValueError('夸克调用需要当前用户原始请求及会话 ID')

    def call(self,args):
        # The official skill requires this preflight before each CLI invocation.
        bash=self.options.get('bash') or shutil.which('bash')
        node=shutil.which('node')
        if not bash or not node:
            raise RuntimeError('缺少 Node 或 Git Bash；不要使用不完整的 WSL bash')
        env=os.environ.copy()
        if os.name=='nt':
            usr_bin=Path(bash).parent.parent/'usr'/'bin'
            env['PATH']=str(usr_bin)+os.pathsep+env['PATH']
        install=subprocess.run([bash,'-c','export PATH=/usr/bin:/bin:$PATH; bash "$1"',
                                'preflight',str(self.skill/'scripts/install.sh')],
                               capture_output=True,text=True,encoding='utf-8',errors='replace',env=env,timeout=180)
        if install.returncode:
            raise RuntimeError('夸克环境检查失败: '+install.stdout[-1200:]+install.stderr[-400:])
        command=[node,str(self.skill/'scripts/quark-drive.cjs'),*args,
                 '--session-input',self.session_input,'--session-id',self.session_id]
        if args[0]=='login':
            process=subprocess.Popen(command,stdout=subprocess.PIPE,text=True,encoding='utf-8',errors='replace',env=env)
            lines=[]
            for line in process.stdout:
                print(line,end='',flush=True)
                lines.append(line)
            return parse_result(''.join(lines),process.wait())
        process=subprocess.run(command,capture_output=True,text=True,encoding='utf-8',errors='replace',
                               env=env,timeout=self.options.get('timeout_seconds',21600))
        if '--help' in args:
            print(process.stdout,process.stderr)
            if process.returncode: raise RuntimeError('CLI help failed')
            return [],{}
        return parse_result(process.stdout,process.returncode)

    def browse(self,parent):
        args=['browse','--all']
        if parent is not None:
            args+=['--parent-fid',str(parent)]
        events,_=self.call(args)
        return self.artifact_items(events)

    @staticmethod
    def artifact_items(events):
        artifacts=[e['data']['file_path'] for e in events if e.get('type')=='artifact']
        if len(artifacts)!=1:
            raise RuntimeError('云端完整列表缺失，不能使用预览代替核验')
        return [json.loads(line) for line in Path(artifacts[0]).read_text(encoding='utf-8').splitlines() if line.strip()]

    def upload(self,path,receipt_path):
        path=Path(path).resolve()
        sha=common.digest_file(path)
        size=path.stat().st_size
        parent=self.options.get('parent_fid')
        # Never infer a root/province folder. Set parent_fid only from user choice.
        receipt_path=Path(receipt_path)
        if receipt_path.exists():
            receipt=json.loads(receipt_path.read_text(encoding='utf-8'))
            if receipt.get('local_sha256')==sha and receipt.get('parent_fid')==parent:
                fid=receipt.get('fid')
                if fid:
                    listed_fid=self.verify(parent,fid,path.name,size)
                    if not listed_fid:
                        raise RuntimeError('已有上传回执，但云端核验不符；禁止盲目重复上传')
                    receipt['cloud_listing_fid']=listed_fid
                    receipt['verified']=True
                    receipt['verification']='fresh_listing_fid_name_size'
                    receipt['full_hash_readback']=False
                    common.write_json(receipt_path,receipt)
                    return receipt
        events,data=self.call(['upload',str(path)]+(['--parent-fid',str(parent)] if parent is not None else []))
        successes=[e['data'] for e in events if e.get('type')=='list' and e.get('code')==0
                   and e.get('data',{}).get('fileName')==path.name]
        if len(successes)!=1 or successes[0].get('fileSize')!=size or not successes[0].get('fileId'):
            raise RuntimeError('上传回执缺失或文件大小不符')
        receipt={'fid':successes[0]['fileId'],'local_sha256':sha,'size':size,
                 'parent_fid':parent,'full_path':data.get('fullPath'),
                 'verified':False,'verification':'pending'}
        common.write_json(receipt_path,receipt)
        listed_fid=self.verify(parent,receipt['fid'],path.name,size)
        if not listed_fid:
            raise RuntimeError('上传后云端文件名/大小核验失败，保留本地文件')
        receipt['cloud_listing_fid']=listed_fid
        receipt['verification']='fresh_listing_fid_name_size'
        receipt['full_hash_readback']=False
        receipt['verified']=True
        common.write_json(receipt_path,receipt)
        return receipt

    def verify_readback(self,fid,sha):
        folder=Path(self.cfg['jobs_dir'])/'cloud_readback'/sha
        folder.mkdir(parents=True,exist_ok=True)
        _,read=self.call(['download','--fid',fid,'--output-dir',str(folder)])
        matches=[f for f in read.get('files',[]) if f.get('fid')==fid and f.get('success')]
        if not matches and read.get('filePath'):
            matches=[read]
        if len(matches)!=1 or common.digest_file(matches[0]['filePath'])!=sha:
            common.write_json(folder/'verification_response.json',read)
            raise RuntimeError('云端回读哈希不符，保留所有本地文件')

    def verify(self,parent,fid,name,size):
        if parent is None:
            if len(name)>50: raise ValueError('文件名超过搜索长度限制，需要指定云端目录 FID 后核验')
            events,_=self.call(['search','--keyword',name,'--stdout-only'])
            items=self.artifact_items(events)
        else:
            items=self.browse(parent)
        # The official CLI can present an opaque listing FID distinct from the
        # upload response FID. Match the unique cloud entry by exact name/size,
        # and retain both identifiers in the receipt for audit.
        matches=[f for f in items if f.get('fid') and f.get('filename')==name and f.get('size')==size]
        return matches[0]['fid'] if len(matches)==1 else None
