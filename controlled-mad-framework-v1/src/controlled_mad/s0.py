from .domain import FrozenS0
from .identity import canonical_hash, select_sender

def freeze_s0(votes,selection):
    if len(votes)!=3 or any(not v.label or not v.comment.strip() for v in votes) or len({v.task_id for v in votes})!=3:
        raise ValueError('S0 必须是三个不同 agent 槽位的有效回复')
    sender=select_sender(selection['dataset_id'],selection['item_id'],selection['seed'])
    refs=tuple(v.parse_id for v in votes)
    return FrozenS0(tuple(votes),sender,refs,canonical_hash([refs,sender]))
