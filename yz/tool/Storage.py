import os
import pickle

from yz.tool.tool import mkdirs

class Storage:
    def __init__(self) -> None:
        self.base_path = './storage/'
        self.msg_locals_path = 'msg_locals.pkl'
        self.msg_locals = {}
        if os.path.exists(self.msg_locals_path):
            self.load_msg_locals()
        if os.path.exists(self.msg_locals_path):
            self.load_msg_locals()
        self.msg = {}

    def save(self, file_path, obj):
        mkdirs(self.base_path)
        with open(os.path.join(self.base_path, file_path), 'wb') as f:
            pickle.dump(obj, f)

    def load(self, file_path):
        with open(os.path.join(self.base_path, file_path), 'wb') as f:
            obj = pickle.load(f)
        return obj

    def save_msg_locals(self):
        self.save(self.msg_locals_path, self.msg_locals)

    def load_msg_locals(self):
        self.msg_locals.update(Storage.load(self.msg_locals_path))
        


