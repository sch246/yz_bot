'''全局计划任务'''
from apscheduler.schedulers.background import BackgroundScheduler
scheduler = BackgroundScheduler()
scheduler.start()


import atexit

def cleanup():
    print("Shutting down scheduler...")
    scheduler.shutdown()

atexit.register(cleanup)
