'''以json格式给每个用户提供存储'''

from main import storage

get_namespace = storage.get_namespace


namespace_users = get_namespace('users')

def storage_get(user_id):
    user_id = str(user_id)
    namespace_users.setdefault(user_id,{})
    return namespace_users[user_id]


def storage_getname(user_id):
    d = storage_get(user_id)
    if 'name' in d.keys():
        return d['name']

def storage_setname(name, user_id):
    d = storage_get(user_id)
    d['name'] = name