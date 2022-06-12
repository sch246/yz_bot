import logging
import os.path
import time
from . import pro_dir

logging.basicConfig(level=logging.INFO, format='[%(asctime)s][%(name)s][%(levelname)s] %(message)s')
logger = logging.getLogger("柚子")
log_path = os.path.join(pro_dir, 'logs')
log_file = os.path.join(log_path, 'laste.log')
if not os.path.isdir(log_path):
    os.mkdir(log_path)
handler = logging.FileHandler(log_file, encoding='utf8')
formatter = logging.Formatter('[%(asctime)s][%(name)s][%(levelname)s] %(message)s')
handler.setFormatter(formatter)
logger.addHandler(handler)


def info(sender: str, msg: str):
    return logger.info(f'[{sender}] '+msg)


def warn(sender: str, msg: str):
    return logger.warning(f'[{sender}] '+msg)


def debug(sender: str, msg: str):
    return logger.debug(f'[{sender}] '+msg)


def close():
    handler.close()
    nowTime = time.strftime('%Y%m%d-%H%M%S')
    if os.path.isfile(log_file):
        os.rename(log_file, os.path.join(log_path, f"{nowTime}.log"))
