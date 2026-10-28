"""材料资格由处理前文本及机械检查决定；旧运行可显式保留模型审核。"""
from .domain import AGENTS
from .identity import canonical_hash
from .material_checks import check_material, reorder
from .protocol import STUDY_V2

def prepare_materials(graph,item,s0,base):
    original_vote=s0.votes[AGENTS.index(s0.sender)]
    original=original_vote.comment; label=original_vote.label
    payload={'task_input':item.task_input,'allowed_labels':list(item.labels),'original':original,'label':label}
    complete_matrix=graph.config.get('complete_matrix',False)
    modern=graph.config.get('study_protocol')==STUDY_V2
    structured=complete_matrix or modern
    records=[]
    for operation in graph.config['operations']:
        attempts=[]; final=None
        max_tries=graph.config['max_generation_attempts'] if operation in ('ethos','logos','pathos','neutral') else 1
        for generation in range(1,max_tries+1):
            ctx={**base,'operation':operation,'generation':generation,'original':original,'label':label}
            if operation in ('ethos','logos','pathos','neutral'):
                request={**payload,'expression':operation}
                if structured and final:
                    request['previous_failure']=final['reason']
                    if final.get('rewritten'): request['previous_candidate']=final['rewritten']
                    ctx['previous_failure']=final['reason']
                task=graph.make('generate',graph.config['material_model'],graph.prompt('rhetoric',request),tuple(v.task_id for v in s0.votes),ctx)
                generated=graph.parsed(task.task_id)
                if generated is None:
                    final={'status':'pending','reason':'等待材料生成'}; break
                attempts.append(task.task_id)
                if generated.get('backend_error'):
                    final={'status':'pending','reason':'材料服务失败，等待显式修复'}; break
                if not generated['valid']:
                    final={'status':'ineligible','reason':'材料生成输出无效'}; continue
                value=generated['value']; rewritten=value['comment']; new_label=value['label']
                if structured:
                    from .matrix import structured_blocks
                    try: structured_blocks(rewritten)
                    except ValueError as error:
                        final={'status':'ineligible','reason':str(error)}; continue
            elif structured:
                from .matrix import reorder_structured
                try: rewritten=reorder_structured(original,operation,graph.config.get('matrix_layout','group_first_v2' if modern else 'slot_first_v1'),normalize=modern)
                except ValueError as error:
                    final={'status':'ineligible','reason':str(error)}; break
                new_label=label
                source_task=original_vote.task_id
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
            checks=check_material(original,rewritten,label,new_label,version=STUDY_V2 if modern else 'legacy_v1')
            if not checks['valid']:
                final={'status':'ineligible','reason':'标签变化或评论为空','checks':checks}; continue
            if checks['no_change']:
                final={'status':'no_change','reason':'R 与 O 完全相同','checks':checks,'rewritten':rewritten}
                if structured and operation in ('ethos','logos','pathos','neutral'): continue
                break
            if graph.config.get('material_policy')=='strict_v10' and (checks['new_numbers'] or checks['new_urls']):
                final={'status':'ineligible','reason':'新增数字或来源 URL，严格策略不予入组','checks':checks,'rewritten':rewritten}; continue
            if not graph.config.get('material_audit',True):
                final={'status':'eligible','reason':'机械检查通过；未启用语义审核','rewritten':rewritten,'checks':checks}
                break
            dependency=source_task if structured and operation not in ('ethos','logos','pathos','neutral') else task.task_id
            audit_context={**ctx,'rewritten':rewritten,'checks':checks,'source_parse':graph.selected[dependency]}
            audit_payload={**payload,'candidate':rewritten,'operation':operation,'mechanical_checks':checks}
            if (graph.config.get('matrix_layout')=='group_first_v2' or modern) and operation in ('support_first','limitation_first'):
                audit_context['matrix_layout']='group_first_v2'
                target='support' if operation=='support_first' else 'limitation'
                audit_payload['operation_definition']=(f'Move ALL {target} paragraphs to the very beginning, preserving their internal order. '
                    'Preserve the original relative order of ALL other paragraphs. Move exact paragraph text only; do not rewrite or remove content. '
                    'Support-first order: Support A / Support B / Conclusion / Limitation. '
                    'Limitation-first order: Limitation / Support A / Conclusion / Support B.')
            audit=graph.make('audit',graph.config['audit_model'],graph.prompt('audit',audit_payload),
                (dependency,),audit_context)
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
        records.append(record)
    seen={original}
    for record in records:
        if complete_matrix and record['status']=='eligible':
            if record['rewritten'] in seen:
                record.update(status='ineligible',reason='变体与原文或其他条件完全重复，不能计为8种材料')
            seen.add(record['rewritten'])
        record['material_id']=canonical_hash(record)
        if record['status']!='pending': graph.put('materials',record['material_id'],record)
    return records
