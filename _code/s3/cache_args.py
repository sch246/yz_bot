'''简单的缓存'''

def cache_args(func):
    '''装饰一个函数，缓存调用结果，但是调用必须是可哈希化的，且不能有关键字参数'''
    results = {}
    def wrapper(*args):
        if args in results:
            return results[args]
        result = func(*args)
        results[args] = result
        return result
    return wrapper