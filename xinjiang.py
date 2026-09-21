"""新疆县级边界的快速选择、计划和本地处理入口。"""
import argparse
import json
import sys
from pathlib import Path

import geopandas as gpd
import geo_common as common
from imagery import download, process
from pipeline import lock

ROOT = Path(__file__).resolve().parent
CATALOG = ROOT / 'boundaries' / 'xinjiang_available.gpkg'

NAMES = {
'650502':'伊州区','650521':'巴里坤哈萨克自治县','650522':'伊吾县','652301':'昌吉市','652302':'阜康市',
'652323':'呼图壁县','652324':'玛纳斯县','652325':'奇台县','652327':'吉木萨尔县','652328':'木垒哈萨克自治县',
'652701':'博乐市','652702':'阿拉山口市','652722':'精河县','652723':'温泉县','652801':'库尔勒市',
'652822':'轮台县','652823':'尉犁县','652824':'若羌县','652825':'且末县','652826':'焉耆回族自治县',
'652827':'和静县','652828':'和硕县','652829':'博湖县','652901':'阿克苏市','652902':'库车市',
'652922':'温宿县','652924':'沙雅县','652925':'新和县','652926':'拜城县','652927':'乌什县',
'652928':'阿瓦提县','652929':'柯坪县','653001':'阿图什市','653022':'阿克陶县','653023':'阿合奇县',
'653024':'乌恰县','653101':'喀什市','653121':'疏附县','653122':'疏勒县','653123':'英吉沙县',
'653124':'泽普县','653125':'莎车县','653126':'叶城县','653127':'麦盖提县','653128':'岳普湖县',
'653129':'伽师县','653130':'巴楚县','653131':'塔什库尔干塔吉克自治县','653201':'和田市','653221':'和田县',
'653222':'墨玉县','653223':'皮山县','653224':'洛浦县','653225':'策勒县','653226':'于田县',
'653227':'民丰县','654002':'伊宁市','654003':'奎屯市','654004':'霍尔果斯市','654021':'伊宁县',
'654022':'察布查尔锡伯自治县','654023':'霍城县','654024':'巩留县','654025':'新源县','654026':'昭苏县',
'654027':'特克斯县','654028':'尼勒克县','654201':'塔城市','654202':'乌苏市','654203':'沙湾市',
'654221':'额敏县','654224':'托里县','654225':'裕民县','654226':'和布克赛尔蒙古自治县','654301':'阿勒泰市',
'654321':'布尔津县','654322':'富蕴县'}

def catalog():
    if not CATALOG.exists():
        raise FileNotFoundError(f'缺少已准备边界目录: {CATALOG}')
    g = gpd.read_file(CATALOG)
    g['dt_adcode'] = g.dt_adcode.astype(str)
    return g

def build_plan(cfg, names):
    g = catalog()
    selected=[]
    for name in names:
        hit=g[(g.dt_name==name)|(g.dt_adcode==str(name))]
        if len(hit)!=1:
            raise ValueError(f'目标不可选或不唯一: {name}')
        selected.append(hit.iloc[0])
    jobs=[]
    for row in selected:
        geom=row.geometry
        jobs.append({'name':row.dt_name,'code':str(row.dt_adcode),'epsg':common.utm_for(geom),
                     'bounds_wgs84':list(geom.bounds)})
    body={'schema':1,'catalog':str(CATALOG),'catalog_sha256':common.digest_file(CATALOG),
          'config_hash':common.job_config_hash(cfg),'source_url':cfg['custom_url'],'jobs':jobs}
    body['plan_id']=common.fingerprint(body)[:20]
    path=Path(cfg['jobs_dir'])/('xinjiang-'+body['plan_id'])/'plan.json'
    common.write_json(path,body)
    return path,body

def run_plan(cfg,path):
    plan=json.loads(Path(path).read_text(encoding='utf-8'))
    check={k:v for k,v in plan.items() if k!='plan_id'}
    if common.fingerprint(check)[:20]!=plan['plan_id'] or common.digest_file(CATALOG)!=plan['catalog_sha256']:
        raise ValueError('任务或边界目录已变化，请重新 plan')
    if plan['config_hash']!=common.job_config_hash(cfg):
        raise ValueError('处理参数已变化，请重新 plan')
    g=catalog()
    with lock(cfg):
        for job in plan['jobs']:
            hit=g[g.dt_adcode==job['code']]
            if len(hit)!=1 or hit.iloc[0].dt_name!=job['name']:
                raise ValueError(f"边界目录与任务不一致: {job['name']}")
            geom=hit.iloc[0].geometry
            work=Path(cfg['jobs_dir'])/('xinjiang-'+plan['plan_id'])/job['code']
            state=work/'state.json'
            try:
                common.write_json(state,{'status':'downloading','job':job})
                tiles=download(cfg,geom)
                common.write_json(state,{'status':'processing','job':job})
                artifact=process(cfg,geom,job['name'],job['code'],work,tiles)
                common.write_json(state,{'status':'awaiting_visual_review','job':job,'artifact':artifact})
            except Exception as exc:
                common.write_json(state,{'status':'failed','job':job,'error':str(exc)})
                raise

def main():
    p=argparse.ArgumentParser(description='新疆77个可选县级边界')
    p.add_argument('--config',default=str(ROOT/'config.yaml'))
    sub=p.add_subparsers(dest='action',required=True)
    sub.add_parser('list')
    pp=sub.add_parser('plan'); pp.add_argument('--name',action='append',required=True)
    rr=sub.add_parser('run'); rr.add_argument('plan')
    a=p.parse_args(); cfg=common.load_config(a.config)
    if a.action=='list':
        g=catalog(); print('\n'.join(f'{i+1:02d}. {r.dt_name} ({r.dt_adcode})' for i,r in g.iterrows()))
    elif a.action=='plan':
        path,plan=build_plan(cfg,a.name); print(json.dumps(plan,ensure_ascii=False,indent=2)); print(f'任务文件: {path}')
    else: run_plan(cfg,a.plan)

if __name__=='__main__':
    try: main()
    except Exception as exc:
        print(f'[失败] {exc}',file=sys.stderr); sys.exit(1)
