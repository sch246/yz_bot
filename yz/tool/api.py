import tool.data as data

class Msg:
    
    @staticmethod
    def send(bot, message, **kargs):
        '''group_id or user_id'''
        bot.use_api('send_msg', 'send_msg', message=message, **kargs)
    @staticmethod
    def recv(bot, message, **kargs):
        bot.recv_event(message=message, **kargs)