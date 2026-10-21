import json
from .domain import HistoryState

def public_comment(message):
    return {'agent':message.sender,'label':message.label,'comment':message.comment}

def extend_history(history,delivery,instruction):
    content=json.dumps({'peer_comments':[public_comment(m) for m in delivery.delivered]},ensure_ascii=False)+'\n'+instruction
    return HistoryState(history.messages+({'role':'user','content':content},),history.seen+tuple(m.message_id for m in delivery.delivered))

def with_reply(history,value,message_id):
    return HistoryState(history.messages+({'role':'assistant','content':json.dumps(value,ensure_ascii=False)},),history.seen+(message_id,))
