"""Recompute a paper-wide Holm family from explicit frozen v2 analyses; no API calls."""
import argparse
import hashlib
import json
from pathlib import Path

from controlled_mad.identity import canonical_hash
from controlled_mad.inference import holm_adjust


def combine(bundles):
    rows=[];seen=set()
    for bundle in bundles:
        if bundle.get('study_protocol')!='continued_peer_exchange_v2': raise ValueError('只合并显式新设计的分析，不混用旧协议')
        for row in bundle.get('inference',[]):
            if not row['primary']: continue
            profile=bundle['model_profiles'][row['model_id']]
            fingerprint=canonical_hash(profile)
            key=(row['dataset_id'],fingerprint,row['operation'])
            if key in seen: raise ValueError('同一数据/模型运行设置/操作重复，不把重复run当成独立模型')
            seen.add(key)
            rows.append({**row,'analysis_id':canonical_hash(bundle),'model_fingerprint':fingerprint,
                         'requested_model':profile.get('model'),'paper_holm_p_value':None})
    estimable=[r for r in rows if r['p_value'] is not None]
    adjusted=holm_adjust([r['p_value'] for r in estimable]+[1.]*(len(rows)-len(estimable)))
    for row,p in zip(estimable,adjusted): row['paper_holm_p_value']=p
    return {'family':'all supplied predeclared dataset/model/operation final-verdict tests; missing tests retain slots',
            'family_size':len(rows),'estimable_tests':len(estimable),'tests':rows,
            'limits':'不跨数据集合并题目、不把模型运行视为重复题目；必须提供全部预定主运行，不能只挑显著或成功的run。'}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--analysis',action='append',required=True,help='显式指定 reports/analysis-<id>.json，可多次传入')
    parser.add_argument('--output',required=True,help='新建输出JSON，不覆盖已有分析')
    args=parser.parse_args();bundles=[]
    for name in args.analysis:
        path=Path(name);raw=path.read_bytes();marker=Path(str(path)+'.committed')
        if not marker.exists() or marker.read_text(encoding='ascii')!=hashlib.sha256(raw).hexdigest(): raise ValueError('冻结分析提交标记或哈希不匹配')
        bundles.append(json.loads(raw))
    result=combine(bundles)
    with Path(args.output).open('x',encoding='utf-8') as f: json.dump(result,f,ensure_ascii=False,indent=2)
    print(json.dumps({'family_size':result['family_size'],'estimable_tests':result['estimable_tests'],'output':args.output},ensure_ascii=False))


if __name__=='__main__': main()
