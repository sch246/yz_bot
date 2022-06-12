import configparser
import os
from . import pro_dir


def getConfig(name, file="config", section="settings", param="auto"):
    config_dir = os.path.join(pro_dir, 'configs', name)
    config = os.path.join(config_dir, file + ".conf")
    if not os.path.isdir(os.path.join(pro_dir, 'configs')):
        os.mkdir(os.path.join(pro_dir, 'configs'))
    if not os.path.isdir(config_dir):
        os.mkdir(config_dir)
    if not os.path.isfile(config):
        file = open(config, 'w')
        file.close()
        conf = configparser.ConfigParser()
        conf.read(config)
        conf.add_section(section)
        conf.set(section, param, 'xxxxx')
        conf.write(open(config, 'w+'))
    else:
        conf = configparser.ConfigParser()
        conf.read(config)
        try:
            value = conf.get(section, param)
            return value
        except:
            conf.set(section, param, 'xxxxx')
            conf.write(open(config, 'w+'))
            return 0
