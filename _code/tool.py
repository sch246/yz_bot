
from typing import Callable

def count_left_spaces(s:str):
    return len(s)-len(s.lstrip())


import inspect



types = {
    str: 'string',
    int: 'number',
    float: 'number',
    bool: 'boolean'
}



# def get_current_weather(location:str, format:str='celsius'):
#     '''
#     Get the current weather

#     location: The city and state, e.g. San Francisco, CA

#     format: The temperature unit to use. Infer this from the users location.
#         enum: ["celsius", "fahrenheit"]
#     '''
#     return "20~25"

def split_list(lst,line):
    striped_list = [l.strip() for l in lst]
    if line in striped_list:
        index = striped_list.index(line)
        return lst[:index], lst[index+1:]
    else:
        return lst, []

class Tool:
    def __init__(self, call:Callable, name:str=None) -> None:
        name = name if name is not None else call.__name__
        self.call = call
        self.description = self.load(call, name)
    def load(self, func=None,name=None):
        func = func if func is not None else self.call
        name = name if name is not None else func.__name__
        # 获取函数的签名
        parameters = inspect.signature(func).parameters


        lines = [line for line in func.__doc__.splitlines() if line.strip()]
        descriptions, lines = split_list(lines,'@param')
        description = '\n'.join(descriptions)
        # 准备基本结构
        result = {
            "type": "function",
            "function": {
                "name": name,
                "description": description.strip(),
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "required": [param.name for param in parameters.values()
                                if param.default is inspect.Parameter.empty]
                }
            }
        }

        base_spaces = count_left_spaces(description)

        params = result["function"]["parameters"]["properties"]
        current_param = None
        for line in lines:
            if count_left_spaces(line)==base_spaces:
                key, description = line.split(':',1)
                current_param = key.strip()
                _type = parameters[current_param].annotation
                if _type==inspect._empty:
                    raise ValueError(f'请给 {inspect.getfile(func)} {func.__name__} {current_param} 设置类型标注')
                params[current_param] = {
                    'type':types[_type],
                    'description': description.strip(),
                }

            elif count_left_spaces(line)>base_spaces:
                sub_key, expr = line.strip().split(':',1)
                params[current_param][sub_key] = eval(expr)

        return result

