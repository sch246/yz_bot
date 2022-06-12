import json
import os
from enum import Enum

from . import pro_dir


class JsonType(Enum):
    DICT = "dict"
    LIST = "list"


class pluginJson:
    _configs_dir = os.path.join(pro_dir, 'configs')
    _file = None
    _json_dir = None
    _json_file = None
    _content = None
    _json = None

    def __init__(self, file: str = "config", plugin: str = "settings", content: JsonType = JsonType.DICT):
        if not os.path.isdir(self._configs_dir):
            os.mkdir(self._configs_dir)
        self._json_dir = os.path.join(self._configs_dir, plugin)
        if not os.path.isdir(self._json_dir):
            os.mkdir(self._json_dir)
        self._json_file = os.path.join(self._json_dir, file) + '.json'
        if content == JsonType.LIST:
            default = "[]"
        else:
            default = "{}"
        if not os.path.isfile(self._json_file):
            with open(self._json_file, 'w') as jsonFile:
                jsonFile.write(default)
                jsonFile.close()
        jsonStr = open(self._json_file, 'r', encoding='utf8').read()
        self._json = json.loads(jsonStr)

    def read(self, key=None):
        if type(self._json) == list:
            return self._json
        elif type(self._json) == dict:
            return self._json[key]

    def write(self, key, value) -> bool:
        if type(self._json) != dict:
            return False
        self._json[key] = value
        try:
            jsonStr = json.dumps(self._json, ensure_ascii=False, indent=4)
            open(self._json_file, 'w+', encoding='utf8').write(jsonStr)
            return True
        except:
            return False

    def appendList(self, value) -> bool:
        if type(self._json) != list:
            return False
        self._json.append(value)
        try:
            jsonStr = json.dumps(self._json, ensure_ascii=False, indent=4)
            open(self._json_file, 'w+', encoding='utf8').write(jsonStr)
            return True
        except:
            return False

    def getKeys(self) -> list:
        if type(self._json) == dict:
            return list(self._json.keys())
        elif type(self._json) == list:
            return self._json.copy()

    def getValues(self) -> list:
        if type(self._json) == dict:
            return list(self._json.values())
        elif type(self._json) == list:
            return self._json.copy()

    def remove(self, key: str):
        if type(self._json) == dict:
            return self._json.pop(key)
        elif type(self._json) == list:
            return self._json.remove(key)

    def reload(self):
        jsonStr = open(self._json_file, 'r', encoding='utf8').read()
        self._json = json.loads(jsonStr)
