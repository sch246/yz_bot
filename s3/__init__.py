'''s3是sch233的缩写)'''

def 字典匹配(expected:dict, result:dict):
    '''输出预期中的键和值是否在结果中都一致'''
    for key in expected.keys():
        if key not in result.keys() or result[key] != expected[key]:
            return False
    return True