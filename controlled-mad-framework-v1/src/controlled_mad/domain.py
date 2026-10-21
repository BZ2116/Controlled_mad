from dataclasses import dataclass, field
from pathlib import Path

AGENTS=('agent_1','agent_2','agent_3')
OPERATIONS=('ethos','logos','pathos','conclusion_front','conclusion_back','support_first','limitation_first')

@dataclass(frozen=True)
class Item:
    dataset_id: str
    item_id: str
    claim: str
    evidence: tuple[str,...]
    labels: tuple[str,...]
    task_input: str=''
    metadata: tuple[tuple[str,str],...]=()
    def __post_init__(self):
        if not self.dataset_id or not self.item_id or not self.claim.strip() or not self.evidence or any(not isinstance(e,str) or not e.strip() for e in self.evidence):
            raise ValueError('题目 ID、主张和证据不能为空')
        if not isinstance(self.task_input,str): raise ValueError('task_input 必须是字符串')
        if not self.task_input:
            rendered='\n'.join(f'- {entry}' for entry in self.evidence)
            object.__setattr__(self,'task_input',f'Claim or question:\n{self.claim}\n\nTask context and evidence:\n{rendered}')
        folded=[x.casefold().strip() for x in self.labels]
        if len(folded)<2 or len(set(folded))!=len(folded) or any(x in ('','missing','no_majority') for x in folded):
            raise ValueError('原生标签集合无效或与保留标签冲突')
        if len({key for key,_ in self.metadata})!=len(self.metadata) or any(not isinstance(key,str) or not isinstance(value,str) for key,value in self.metadata):
            raise ValueError('metadata 必须是唯一键值字符串对')

@dataclass(frozen=True)
class GoldRecord:
    dataset_id: str
    item_id: str
    label: str

@dataclass(frozen=True)
class DatasetBundle:
    items: tuple[Item,...]
    gold: tuple[GoldRecord,...]
    source_hash: str
    rejected: tuple[dict,...]=()

@dataclass(frozen=True)
class ModelProfile:
    profile_id: str
    provider: str='mock'
    model: str='mock-v1'
    base_url: str=''
    credential_env: str=''
    temperature: float|None=0.7
    max_tokens: int=1024
    seed: int|None=None
    supports_seed: bool=False
    timeout: float=60
    context_limit: int=128000
    expected_model: str=''
    max_tokens_field: str='max_tokens'
    reasoning_effort: str|None=None

@dataclass(frozen=True)
class Task:
    task_id: str
    stage: str
    profile_id: str
    messages: tuple[dict,...]
    dependencies: tuple[str,...]
    context: dict
    prompt_hash: str

@dataclass(frozen=True)
class ModelResult:
    raw: bytes
    text: str
    returned_model: str|None
    stop_reason: str|None
    usage: dict|None
    error: str|None=None

@dataclass(frozen=True)
class Vote:
    label: str
    comment: str
    task_id: str=''
    attempt_id: str=''
    parse_id: str=''

@dataclass(frozen=True)
class FrozenS0:
    votes: tuple[Vote,...]
    sender: str
    source_ids: tuple[str,...]
    s0_hash: str

@dataclass(frozen=True)
class MaterialPair:
    original: str
    rewritten: str
    label: str
    operation: str
    source_hash: str
    status: str
    provenance: tuple[str,...]=()

@dataclass(frozen=True)
class Message:
    message_id: str
    sender: str
    round_no: int
    label: str
    comment: str
    source: str
    version: str='original'

@dataclass(frozen=True)
class Delivery:
    recipient: str
    round_no: int
    delivered: tuple[Message,...]
    blocked: tuple[str,...]=()

@dataclass(frozen=True)
class HistoryState:
    messages: tuple[dict,...]
    seen: tuple[str,...]

@dataclass(frozen=True)
class Snapshot:
    snapshot_id: str
    round_no: int
    replies: tuple[str,...]
    histories: dict
    parent: str|None=None

@dataclass(frozen=True)
class Comparison:
    comparison_id: str
    context: dict
    cells: dict

@dataclass(frozen=True)
class Intervention:
    rule_id: str
    source_agent: str
    target_agent: str
    delivery_round: int
    def __post_init__(self):
        if self.source_agent==self.target_agent or self.source_agent not in ('S','A','B') or self.target_agent not in ('S','A','B') or self.delivery_round not in (2,3):
            raise ValueError('干预必须是 T2/T3 的一条不同角色消息边')

@dataclass(frozen=True)
class RunHandle:
    path: Path
