import logging
import logging.handlers

# 创建一个根logger
logger = logging.getLogger()
logger.setLevel(logging.INFO)

# 创建文件处理器（每天一个日志文件）
handler = logging.handlers.TimedRotatingFileHandler(
    filename='app.log',
    when='midnight',  # 每天午夜切换文件
    interval=1,       # 每天
    backupCount=7     # 保留7天的日志
)

# 创建格式化器
formatter = logging.Formatter(
    '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
handler.setFormatter(formatter)

# 添加处理器
logger.addHandler(handler)
