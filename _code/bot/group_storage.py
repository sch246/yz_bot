'''以json格式给每个群提供存储'''

from main import storage

def storage_get(group_id):
    return storage.get('groups',str(group_id))


def storage_getname(group_id):
    d = storage_get(group_id)
    if 'name' in d.keys():
        return d['name']

def storage_setname(name, group_id):
    d = storage_get(group_id)
    d['name'] = name
