import json
import traceback

from chat import Chat


chat = Chat()

def get_current_weather(location:str, format:str='celsius'):
    '''
    Get the current weather

    location: The city and state, e.g. San Francisco, CA

    format: The temperature unit to use. Infer this from the users location.
        enum: ["celsius", "fahrenheit"]
    '''
    return "晴 -20~-10"

def get_location():
    '''
    Get the user's location
    '''
    return "beijing"

from bs4 import BeautifulSoup
import requests
def exec_code(code:str, expr:str):
    '''
    execute a real-time python code. BeautifulSoup and requests are imported. 你应该可以用python读取和编辑`data`字典，其中的数据会被持久化保存

    code: The code to execute

    expr: The value to be returned
    '''
    dic = globals()
    exec(code,dic)
    return repr(eval(expr,dic))



try:
    data = json.load(open('data.json','r',encoding='utf-8'))
except:
    data = {}
import atexit
atexit.register(lambda:json.dump(data, open('data.json','w',encoding='utf-8'),ensure_ascii=False,indent=4))


chat.add_tool(get_current_weather)
# chat.add_tool(get_location)
chat.add_tool(exec_code)

# chat.add(chat.req())

print()
print("Enter to confirm")
print("type ''' to input muti lines")
print()

last_data = None
def show_data(s):
    global last_data
    result = 'data内的键: {'+', '.join([f"{k}: {type(v)}" for k, v in data.items()])+'}' if data!=last_data else None
    last_data = data.copy()
    return result

chat.set_settings(["Don't make assumptions about what values to plug into functions. Ask for clarification if a user request is ambiguous.",
                   show_data,
                   ])

hold = False
lines = []
print('\033[32muser: ',end='',flush=True)

atexit.register(lambda:print('\033[0m\nbye'))
try:
    while True:
        line = input()
        if line == "'''":
            hold = not hold
            line = '\n'.join(lines[1:])
            lines = []
        if hold:
            lines.append(line)
            continue
        print('\033[0m')
        chat.call({'role':'user','content':line})
        print('\033[32muser: ',end='',flush=True)
except KeyboardInterrupt:
    exit()
