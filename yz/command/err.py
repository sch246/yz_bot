from tool.cmd import std_cmd



class CMDError(Exception):
    '''手动抛出此异常'''
    def __init__(self, value):
        self.value = value
    def __str__(self):
        return (f"命令异常: {repr(self.value)}")

class err:
    '''格式: .err raise
    用途: 创建一个异常，测试用'''
    docs={
    'run':__doc__,
    'err':'''格式: .err [pass | <limit>]
    用途: 忽略异常继续运行, 或打印traceback
    不输入或输入0返回全部traceback，正数输入前n行，负数输入后n行'''
    }
    level=4
    
    @classmethod
    @std_cmd
    def run(cls):
        return {
            ' raise': cls.raise_err,
        }

    @classmethod
    @std_cmd
    def err(cls):
        return {
            ' (\-?[0-9]+)': cls.traceback_err,
            ' pass': cls.pass_err,
        }

    def raise_err(bot,match):
        print('触发err')
        raise CMDError('.err raise')

    def traceback_err(bot,match):
        i = int(match.group(1))
        tb_list = bot.state[1].splitlines()
        tb_len = len(tb_list)
        if -tb_len < i < 0:
            return '\n'.join(tb_list[i:])
        elif 0 < i < tb_len:
            return '\n'.join(tb_list[:i])
        else:
            return '\n'.join(tb_list)
    
    def pass_err(bot,match):
        bot.set_state(('run',))
        return '继续运行'
        
      
                
