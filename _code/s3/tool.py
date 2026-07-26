
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
#
#     @param
#     location: The city and state, e.g. San Francisco, CA
#
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
    def load(self, func=None, name=None):
        func = func if func is not None else self.call
        name = name if name is not None else func.__name__
        # 获取函数的签名
        parameters = inspect.signature(func).parameters

        # 解析文档字符串
        doc_lines = [line.strip() for line in func.__doc__.splitlines() if line.strip()]
        
        # 准备基本结构
        result = {
            "type": "function",
            "function": {
                "name": name,
                "description": "",
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "required": [param.name for param in parameters.values()
                               if param.default is inspect.Parameter.empty]
                }
            }
        }

        # 解析文档
        current_section = None
        params = result["function"]["parameters"]["properties"]
        
        for line in doc_lines:
            if line.startswith('Args:') or line.startswith('@param'):
                current_section = 'args'
                continue
            elif line.startswith('Returns:'):
                current_section = 'returns'
                continue
                
            if current_section is None:
                # 主描述部分
                if result["function"]["description"]:
                    result["function"]["description"] += "\n" + line
                else:
                    result["function"]["description"] = line
            elif current_section == 'args':
                # 参数部分
                if ':' in line:
                    param_name, param_desc = line.split(':', 1)
                    param_name = param_name.strip()
                    if param_name not in parameters:
                        continue
                        
                    _type = parameters[param_name].annotation
                    if _type == inspect._empty:
                        raise ValueError(f'请给 {inspect.getfile(func)} {func.__name__} {param_name} 设置类型标注')
                        
                    params[param_name] = {
                        'type': types[_type],
                        'description': param_desc.strip()
                    }
                    
                    # 检查是否有枚举值说明
                    if 'enum:' in param_desc:
                        enum_start = param_desc.find('[')
                        enum_end = param_desc.find(']')
                        if enum_start != -1 and enum_end != -1:
                            enum_str = param_desc[enum_start+1:enum_end]
                            enum_values = [v.strip(' "\'') for v in enum_str.split(',')]
                            params[param_name]['enum'] = enum_values

        return result

