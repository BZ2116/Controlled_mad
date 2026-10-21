"""材料资格由处理前文本及机械检查决定；旧运行可显式保留模型审核。"""
from .domain import AGENTS
from .identity import canonical_hash
from .material_checks import check_material, reorder

def prepare_materials(graph,item,s0,base):
    original_vote=s0.votes[AGENTS.index(s0.sender)]
    original=original_vote.comment; label=original_vote.label
    payload={'task_input':item.task_input,'allowed_labels':list(item.labels),'original':original,'label':label}
    records=[]
    for operation in graph.config['operations']:
        attempts=[]; final=None
        max_tries=graph.config['max_generation_attempts'] if operation in ('ethos','logos','pathos') else 1
        for generation in range(1,max_tries+1):
            ctx={**base,'operation':operation,'generation':generation,'original':original,'label':label}
            if operation in ('ethos','logos','pathos'):
                task=graph.make('generate',graph.config['material_model'],graph.prompt('rhetoric',{**payload,'expression':operation}),tuple(v.task_id for v in s0.votes),ctx)
                generated=graph.parsed(task.task_id)
                if generated is None:
                    final={'status':'pending','reason':'等待材料生成'}; break
                attempts.append(task.task_id)
                if generated.get('backend_error'):
                    final={'status':'pending','reason':'材料服务失败，等待显式修复'}; break
                if not generated['valid']:
                    final={'status':'ineligible','reason':'材料生成输出无效'}; continue
                value=generated['value']; rewritten=value['comment']; new_label=value['label']
            else:
                actx={k:v for k,v in ctx.items() if k not in ('operation','generation')}
                task=graph.make('annotate',graph.config['material_model'],graph.prompt('annotate',payload),tuple(v.task_id for v in s0.votes),actx)
                generated=graph.parsed(task.task_id)
                if generated is None:
                    final={'status':'pending','reason':'等待原文块注释'}; break
                attempts.append(task.task_id)
                if generated.get('backend_error'):
                    final={'status':'pending','reason':'注释服务失败，等待显式修复'}; break
                if not generated['valid']:
                    final={'status':'ineligible','reason':'原文块注释无效'}; break
                try: rewritten=reorder(original,generated['value']['blocks'],operation)
                except ValueError as error:
                    final={'status':'ineligible','reason':str(error)}; break
                new_label=label
            checks=check_material(original,rewritten,label,new_label)
            if not checks['valid']:
                final={'status':'ineligible','reason':'标签变化或评论为空','checks':checks}; continue
            if checks['no_change']:
                final={'status':'no_change','reason':'R 与 O 完全相同','checks':checks,'rewritten':rewritten}; break
            if not graph.config.get('material_audit',True):
                final={'status':'eligible','reason':'机械检查通过；未启用语义审核','rewritten':rewritten,'checks':checks}
                break
            audit_context={**ctx,'rewritten':rewritten,'checks':checks,'source_parse':graph.selected[task.task_id]}
            audit=graph.make('audit',graph.config['audit_model'],graph.prompt('audit',{**payload,'candidate':rewritten,'operation':operation,'mechanical_checks':checks}),
                (task.task_id,),audit_context)
            verdict=graph.parsed(audit.task_id)
            if verdict is None:
                final={'status':'pending','reason':'等待自动审核'}; break
            attempts.append(audit.task_id)
            if verdict.get('backend_error'):
                final={'status':'pending','reason':'审核服务失败，等待显式修复'}; break
            if not verdict['valid']:
                final={'status':'ineligible','reason':'审核输出无效','checks':checks}; continue
            decision=verdict['value']
            final={'status':'eligible' if decision['verdict']=='pass' else 'ineligible','reason':decision['reason'],'audit_verdict':decision['verdict'],
                'rewritten':rewritten,'checks':checks,'audit_parse':graph.selected[audit.task_id]}
            if final['status']=='eligible': break
        record={**base,'operation':operation,'original':original,'label':label,'source_hash':s0.s0_hash,'attempt_tasks':attempts,**final}
        record['material_id']=canonical_hash(record)
        if record['status']!='pending': graph.put('materials',record['material_id'],record)
        records.append(record)
    return records
