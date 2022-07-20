from . import pro_dir, log
from threading import Thread
import os
import imp


class pluginsManager:
    plugin_dir = os.path.join(pro_dir, 'plugins')
    if not os.path.isdir(plugin_dir):
        os.mkdir(plugin_dir)
    tmp = os.listdir(plugin_dir)
    if '__pycache__' in tmp:
        tmp.remove('__pycache__')
    if '__init__.py' in tmp:
        tmp.remove('__init__.py')
    if '__main__.py' in tmp:
        tmp.remove('__main__.py')
    plugin_list = []
    for i in tmp:
        if '.py' in i:
            plugin_list.append(os.path.join(plugin_dir, i))
        else:
            y = os.listdir(os.path.join(plugin_dir, i))
            if '__init__.py' in y:
                y.remove('__init__.py')
            if '__main__.py' in y:
                y.remove('__main__.py')
            for n in range(len(y)):
                if '.py' in y[n]:
                    plugin_list.append(os.path.join(plugin_dir, i, y[n]))

    def __init__(self):
        log.info(sender='插件管理', msg='插件管理器已启动')

    def runPlugins(self):
        for plugin in self.plugin_list:
            function = imp.load_source('plugin-' + str(plugin), plugin)
            try:
                log.info(sender='插件管理', msg=f'插件{function.pluginName}加载中')
                Thread(target=function.onStart(), args=('Thread-'+str(plugin), plugin)).start()
            except:
                log.warn(sender='插件管理', msg=f'插件{function.pluginName}加载失败')
        if len(self.plugin_list) == 0:
            log.warn(sender='插件管理', msg='未找到插件')

    def stopPlugins(self):
        for plugin in self.plugin_list:
            function = imp.load_source('plugin-' + str(plugin), plugin)
            try:
                function.onStop()
            except:
                log.warn(sender='插件管理', msg=f'插件{function.pluginName}卸载失败')
