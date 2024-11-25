'''
用法：在工作目录创建文件.sync
每行写文件夹名字(可以用相对目录，但是请不要包含空格)和想要同步到的地方(用户@ip:目录)
只考虑了本地是windows且远程是linux的情况
例如这样
a_test root@example.com:/opt/mc/1.20.2/datapacks/a_test
fooPack root@127.0.0.1:/opt/mc/1.20.2/datapacks/fooPack
a_test E:\mc\.minecraft\versions\1.20.2\saves\新的世界\datapacks\a_test
fooPack E:\mc\.minecraft\versions\1.20.2\saves\新的世界\datapacks\fooPack

它会首先同步一次(删除远程的文件夹，然后把本地的复制过去)
随后，在文件被修改时自动同步过去
需要放一堆文件远程传输时，尽量不要一次放一堆文件，而是放一个包含一堆文件的文件夹（这样会被压缩传输）

需要前置:
pip install watchdog paramiko scp
'''
#TODO delete加create时改成move(无解)
初始同步 = True     # 运行时是否先整个同步一次
压缩阈值 = 9        # 创建文件夹时，若子文件数超过这个数量，则压缩后发送，若设为负数则始终压缩(哪怕是空文件夹)
事件延时 = 0.1        # 接收到事件后多久才运行（秒），如果设置太短会导致重复发送不必要的文件，设置太长会导致很慢

import os
import time
from typing import Callable

from queue import Queue

from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
from watchdog import events
import tarfile
import atexit

import os
from paramiko import SSHClient, AutoAddPolicy
from scp import SCPClient

import shutil

from threading import Thread

# def tee(obj):
#     print(obj)
#     return obj

def get_dir(path):
    '''获取父目录'''
    return os.path.split(path.rstrip("/"))[0]

def collect_dir(directory):
    '''获取所有子文件和子文件夹路径'''
    all_files = []
    all_dirs = []
    for root, dirs, files in os.walk(directory):
        all_files += [os.path.join(root,file) for file in files]
        all_dirs += [os.path.join(root,dir) for dir in dirs]
    return all_files, all_dirs
def count_files(directory):
    '''获取所有子文件数量'''
    files_count = 0
    for _, _, files in os.walk(directory):
        files_count += len(files)
    return files_count

def create_tarfile(source_dir):
    '''创建tar文件'''
    tar_file = 'tmp.tar'
    with tarfile.open(tar_file, "w") as tar:
        tar.add(source_dir, arcname=os.path.basename(source_dir))
    return tar_file

def is_local(host:str):
    return len(host)==1 and ('A' <= host <= 'Z' or 'a' <= host <= 'z')

def to_thread(func, ret_thread=False):
    '''
    此装饰器不打算获取返回值
    '''
    def wrapper(*args, **kargs):
        thread = Thread(target=func, args=args, kwargs=kargs)
        thread.daemon = True
        thread.start()
        if ret_thread:
            return thread
    return wrapper

@to_thread
def set_timeout(func, sec):
    time.sleep(sec)
    func()

class FolderEventHandler(FileSystemEventHandler):
    def __init__(self, local_folder, host, remote_folder) -> None:
        self.local_folder, self.host, self.remote_folder = local_folder, host, remote_folder
        self.ignore_rename_modified = []
        self.ignore_created = []

        self.events: Queue[tuple[Callable, events.FileSystemEvent]] = Queue()
        self.event_times: Queue[float] = Queue()
        self._loop()

    def dispatch(self, event:events.FileSystemEvent):
        """
        覆写事件，让它等个一会再发
        """
        action = {
            events.EVENT_TYPE_CREATED: self.on_created,
            events.EVENT_TYPE_DELETED: self.on_deleted,
            events.EVENT_TYPE_MODIFIED: self.on_modified,
            events.EVENT_TYPE_MOVED: self.on_moved,
            events.EVENT_TYPE_CLOSED: self.on_closed,
            events.EVENT_TYPE_OPENED: self.on_opened,
        }[event.event_type]

        if event.event_type==events.EVENT_TYPE_MODIFIED and event.is_directory:
            # 目录修改表示下面的文件变动，跳过
            return
        for e in list(reversed(list(self.events.queue))):
            if event.event_type==events.EVENT_TYPE_MODIFIED:
                # 如果前面(事件延时内)存在同一个文件的modify或create事件，则跳过
                if (e[1].src_path==event.src_path and
                    e[1].event_type in [events.EVENT_TYPE_MODIFIED, events.EVENT_TYPE_CREATED]):
                    return

        # print(len(self.events.queue), event.event_type)
        self.events.put((action, event))
        self.event_times.put(time.time()+事件延时)
        # self.on_any_event(event)

    @to_thread
    def _loop(self):
        while True:
            t = self.event_times.get()
            if t>time.time():
                time.sleep(t-time.time())
            action, event = self.events.get()
            action(event)

    def _to_remote(self, local_path):
        if local_path==self.local_folder:
            return self.remote_folder
        return os.path.join(self.remote_folder, os.path.relpath(local_path, self.local_folder)).replace("\\", "/")

    def on_deleted(self, event):
        # windows 移动出去文件时会触发自身的deleted
        self._delete(event.src_path)

    def on_created(self, event):
        # windows 创建目录时会递归触发子目录和子文件的create(之后)     (用ignore_created标记并且排除掉)
        # windows 移动进来文件时会递归触发父目录(先)和自身的modify(后)   (取消目录modify的动作，并且排除太近的modify)
        if event.is_directory:
            files, dirs = collect_dir(event.src_path)
            self.ignore_created += files + dirs
            zipped =  len(files) > 压缩阈值 # 如果子文件超过这个数量，就压缩发送
        else:
            zipped = False
        if event.src_path in self.ignore_created:
            self.ignore_created.remove(event.src_path)
        else:
            self._create(event.src_path, zipped)

    def on_moved(self, event):
        # windows 重命名目录时，会先触发子目录子文件的move  (检测旧的父目录是否存在 跳过)
        # windows 重命名文件时，会触发目标文件的modify   (用ignore_rename_modified标记并且排除掉)
        if not event.is_directory:
            self.ignore_rename_modified.append(event.dest_path)
        if os.path.isdir(get_dir(event.src_path)):
            self._move(event.src_path, event.dest_path)
        # else:
        #     print('move skipped', event.dest_path)

    def on_modified(self, event):
        # 必定是文件的 (因为目录的已经排除掉了)
        if event.src_path in self.ignore_rename_modified:
            self.ignore_rename_modified.remove(event.src_path)
        else:
            self._modify(event.src_path)



class ObserveToLocal(FolderEventHandler):

    def _delete(self, local_path):
        remote_path = f'{self.host}:{self._to_remote(local_path)}'
        if not os.path.exists(remote_path):
            return
        if os.path.isfile(remote_path):
            os.remove(remote_path)
        else:
            shutil.rmtree(remote_path)
        print(f"Deleted: {remote_path}")

    def _create(self, local_path, zipped):
        remote_path = f'{self.host}:{self._to_remote(local_path)}'
        if os.path.isfile(local_path):
            shutil.copy(local_path, remote_path)
        else:
            shutil.copytree(local_path, remote_path, dirs_exist_ok=True)
        print(f"Created: {local_path} -> {remote_path}")

    def _move(self, old_local_path, new_local_path):
        old_remote_path = f'{self.host}:{self._to_remote(old_local_path)}'
        new_remote_path = f'{self.host}:{self._to_remote(new_local_path)}'
        os.rename(old_remote_path, new_remote_path)
        print(f"Moved: {old_remote_path} -> {new_remote_path}")

    def _modify(self, local_path):
        remote_path = f'{self.host}:{self._to_remote(local_path)}'
        if os.path.isfile(local_path):
            shutil.copy(local_path, remote_path)
        else:
            shutil.copytree(local_path, remote_path, dirs_exist_ok=True)
        print(f"Modified: {local_path} -> {remote_path}")



class ObserveToRemote(FolderEventHandler):
    def __init__(self, local_folder, host, remote_folder) -> None:
        super().__init__(local_folder, host, remote_folder)

        ssh = SSHClient()
        ssh.set_missing_host_key_policy(AutoAddPolicy())

        username, hostname = host.split('@')
        ssh.connect(hostname, 22, username)
        self.ssh = ssh

        self.__scp = SCPClient(ssh.get_transport())

        atexit.register(self.ssh.close)
        atexit.register(self.__scp.close)

    def sh(self, cmd):
        _, _, stderr = self.ssh.exec_command(cmd)
        errinfo = stderr.read().decode("utf-8")
        if errinfo:
            print(errinfo)

    def _scp(self, local_path, remote_path):
        '''
        local_path: dir / file
        host_and_path: host:dir / host:file

        file / dir -> file    替换
        file / dir -> dir     放进文件夹
        '''
        # self.sh(f'mkdir -p {get_dir(remote_path)}')
        if os.path.isdir(local_path):
            self.__scp.put(local_path,remote_path,recursive=True)
        else:
            self.__scp.put(local_path,remote_path)

    def _rm(self, remote_path):
        self.sh(f"rm -rf '{remote_path}'")

    def _mv(self, src, dst):
        self.sh(f"mv '{src}' '{dst}'")

    def _untar(self, remote_tar, remote_dir):
        self.sh(f"tar -xf '{remote_tar}' -C '{remote_dir}'")

    def _delete(self, local_path):
        # 删除远程 Linux 上的文件或文件夹
        remote_path = self._to_remote(local_path)
        self._rm(remote_path)
        print(f"Deleted: {self.host}:{remote_path}")

    def _create(self, local_path, zipped):
        remote_path = self._to_remote(local_path)
        if zipped:
            local_tar = create_tarfile(local_path)
            remote_tar = self._to_remote(local_path).rstrip('/')+'.tar'
            self._scp(local_tar, remote_tar)
            self._untar(remote_tar, get_dir(remote_path))
            self._rm(remote_tar)
            os.remove(local_tar)
        else:
            self._scp(local_path, remote_path)
        print(f"Created: {local_path} -> {self.host}:{remote_path}")

    def _move(self, old_local_path, new_local_path):
        old_remote_path = self._to_remote(old_local_path)
        new_remote_path = self._to_remote(new_local_path)
        self._mv(old_remote_path, new_remote_path)
        print(f"Moved: {self.host}:{old_remote_path} -> {self.host}:{new_remote_path}")

    def _modify(self, local_path):
        remote_path = self._to_remote(local_path)
        self._scp(local_path, remote_path)
        print(f"Modified: {local_path} -> {self.host}:{remote_path}")


def setting_filter(lines:str):
    return [*filter(None, [line.split('//', 1)[0].strip() for line in lines])]
with open('.sync',encoding='utf-8') as f:
    settings = f.read().splitlines()

# 监视文件夹
observer = Observer()
for line in setting_filter(settings):
    local_folder, remote_host_and_folder = line.split(' ', 1)
    remote_host_and_folder = remote_host_and_folder.strip()

    remote_host, remote_folder = remote_host_and_folder.split(':', 1)

    if is_local(remote_host):
        handler = ObserveToLocal(local_folder, remote_host, remote_folder)
    else:
        handler = ObserveToRemote(local_folder, remote_host, remote_folder)

    # if 初始同步:
    #     handler._delete(local_folder)
    #     handler._create(local_folder, True)
    observer.schedule(handler, local_folder, recursive=True)

observer.start()

try:
    while True:
        time.sleep(1)
except KeyboardInterrupt:
    observer.stop()

observer.join()
