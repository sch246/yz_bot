'''以json格式给每个用户提供存储'''

from main import storage


def storage_get(user_id):
    return storage.get('users',str(user_id))


def storage_getname(user_id):
    d = storage_get(user_id)
    if 'name' in d.keys():
        return d['name']

def storage_setname(name, user_id):
    d = storage_get(user_id)
    d['name'] = name
