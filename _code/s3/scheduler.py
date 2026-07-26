'''全局计划任务'''
from apscheduler.schedulers.background import BackgroundScheduler
scheduler = BackgroundScheduler()
scheduler.start()


import atexit

def shutdown():
    """停止接收新任务并等待正在执行的任务结束；可重复调用。"""
    if not scheduler.running:
        return
    print("Shutting down scheduler...")
    scheduler.shutdown(wait=True)

atexit.register(shutdown)
