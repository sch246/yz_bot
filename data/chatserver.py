import openai
openai.api_key='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'
from queue import Queue
from multiprocessing.managers import BaseManager


calls_queue = Queue()
def get_calls_queue():
    return calls_queue
backs_queue = Queue()
def get_backs_queue():
    return backs_queue

class QueueManager(BaseManager):
    ...

QueueManager.register('get_calls', callable=get_calls_queue)
QueueManager.register('get_backs', callable=get_backs_queue)

manager = QueueManager(address=('0.0.0.0',2333), authkey=b'sch233')

manager.start()

calls=manager.get_calls()
backs=manager.get_backs()

import atexit
atexit.register(manager.shutdown)

print('started.')

while True:
    call = calls.get()
    print('>',call)
    if call==None:break
    reply = openai.ChatCompletion.create(
            model="gpt-3.5-turbo",
            messages=call
            )['choices'][0]['message']['content']
    print('<',reply)
    backs.put(reply)


