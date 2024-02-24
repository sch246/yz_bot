'''帮助命令'''

from main import getcmd, cmds

def run(body:str):
    '''帮助命令
格式: .help[ <命令>]'''
    name = body.strip()
    if not name:
        return '\n'.join([f'{name}\n    {model.__doc__.strip()}' for name, model in cmds.modules.items()])
    cmd = getcmd(name)
    if cmd:
        return cmd.run.__doc__
    return '该命令不存在！'

