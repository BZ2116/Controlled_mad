from .domain import Delivery

def route(history,round_no,protocol,recipient,intervention=None,peers=()):
    delivered=[]; blocked=[]
    for message in peers:
        if message.sender==recipient or message.message_id in history.seen: continue
        if round_no>1 and protocol=='I': continue
        if intervention and intervention['round']==round_no and intervention['source']==message.sender and intervention['target']==recipient:
            blocked.append(message.message_id); continue
        delivered.append(message)
    return Delivery(recipient,round_no,tuple(delivered),tuple(blocked))
