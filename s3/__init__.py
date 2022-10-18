'''s3是sch233的缩写)'''

from typing import Callable
import sys

def 字典匹配(expected:dict, result:dict):
    '''输出预期中的键和值是否在结果中都一致'''
    for key in expected.keys():
        if key not in result.keys() or result[key] != expected[key]:
            return False
    return True

def n(f:Callable):
    def _(*args, **kwargs):
        return not f(*args, **kwargs)
    return _

in_debug = 'debug' in sys.argv[1:]
def debug(*args):
    if in_debug:
        print(*args)