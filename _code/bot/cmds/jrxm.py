'''今日小猫'''
import re
import time
import os
import traceback

from main import storage, getstorage, random, is_msg, getname, getgroupstorage, getgroupname, headshot, read_params, cache, cq

def run(body:str):
    '''今日小猫都是谁呢？
格式:
.jrxm           # 今日小猫
.jrxm <int>  # 按顺序读取今日小猫
.jrxm list      # 获取今日小猫列表'''
    groupmode = False
    re_int = re.compile(r'(-?\d+)$')
    qq = cache.thismsg()['user_id']
    sender = getname()
    with open('data/bbxm.txt', 'r', encoding='utf-8') as f:
        lines = f.readlines()

    date = time.strftime('%y-%m-%d')
    data = getstorage()
    data_jrxm = storage.get('','jrxm')
    jrxm_list = data_jrxm['jrxm']
    number = 0
    try:
        data_group = getgroupstorage()
        groupmode = True
    except Exception as e:
        groupmode = False
        
    if not body.strip():
        if not data_jrxm.get('jrxm_date')==date:

            data_jrxm['jrxm_date'] = date
            data_jrxm['jrxm'].clear()
            if not data.get('jrxm_date')==date:
                data['jrxm_date'] = date
                data['jrxm'] = random.choice(lines).strip()
                data_jrxm['jrxm'].append(qq)
                number = len(data_jrxm['jrxm'])
            else:
                data_jrxm['jrxm'].append(qq)
                number = len(data_jrxm['jrxm'])
        else:
            if not data.get('jrxm_date')==date:
                data['jrxm_date'] = date
                data['jrxm'] = random.choice(lines).strip()
                if qq not in data_jrxm['jrxm']:
                    data_jrxm['jrxm'].append(qq)
                    number = len(data_jrxm['jrxm'])
                else:
                    number = jrxm_list.index(qq) + 1
            else:
                if qq not in data_jrxm['jrxm']:
                    data_jrxm['jrxm'].append(qq)
                    number = len(data_jrxm['jrxm'])
                else:
                    number = jrxm_list.index(qq) + 1
        
        r = data['jrxm']
        if groupmode:
            if time.strftime('%m-%d')!='04-01':
                return f'今日小猫（第{number}只）是\n{sender}！\n{headshot(qq)}\n今天{r}!'
            else:
                return f'今日鸽子（第{number}只）是\n{sender}！\n{headshot(qq)}\n今天只有柚子是小猫！www！'
        else:
            return f'今日小猫是\n{sender}！\n{headshot(qq)}\n今天{r}!'

    s, body = read_params(body)
    try:
        if re_int.match(s):
            s = int(s)
            if not data_jrxm.get('jrxm_date')==date:
                data_jrxm['jrxm_date'] = date
                data_jrxm['jrxm'].clear()
            number = len(data_jrxm['jrxm'])
            if groupmode:
                if time.strftime('%m-%d')!='04-01':
                    if s < 1:
                        return f'这里还没有小猫呢'
                    elif len(data_jrxm['jrxm']) > s-1:

                        qq = data_jrxm['jrxm'][s-1]
                        r_nxm = getstorage(qq)['jrxm'].replace('你', '它')
                        return f'今天第{s}只小猫是\n{getname(qq)}！\n{headshot(qq)}\n↑今天{r_nxm}'
                    else:
                        if s == 1:
                            return f'今天还没有小猫呢'
                        
                        else:
                            return f'这里还没有小猫呢'
                else:
                    if s < 1:
                        return f'这里还没有鸽子呢'
                    elif len(data_jrxm['jrxm']) > s-1:

                        qq = data_jrxm['jrxm'][s-1]
                        r_nxm = getstorage(qq)['jrxm'].replace('你', '它')
                        return f'今天第{s}只鸽子是\n{getname(qq)}！\n{headshot(qq)}\n是什么鸽子呢？'        
                    else:
                        if s == 1:
                            return f'今天还没有鸽子呢'
                        
                        else:
                            return f'这里还没有鸽子呢'
            else:
                return f'世界就是绕着你打转！'

        elif s=='list':
            if not data_jrxm.get('jrxm_date')==date:
            
                data_jrxm['jrxm_date'] = date
                data_jrxm['jrxm'].clear()

                
            number = len(data_jrxm['jrxm'])
            
            if groupmode:
                if time.strftime('%m-%d')!='04-01':
                    if len(data_jrxm['jrxm']) > 0:
                        jrxm_result = [f"{cq.url2cq(f'http://q1.qlogo.cn/g?b=qq&nk={jrxm_l}&s=1')}   ↖{getstorage(jrxm_l)['jrxm'].replace('你', '它')[2:]}" for jrxm_l in jrxm_list]
                        result = "\n".join(jrxm_result)
                        if len(data_jrxm['jrxm']) > 5:
                            return f'今日小猫（们）：\n{result}\n\n今天真是小猫大军呢...'
                        else:
                            return f'今日小猫（们）：\n{result}'
                    else:
                        return f'今天还没有小猫呢'
                else:
                    if len(data_jrxm['jrxm']) > 0:
                        jrxm_result = [f"{cq.url2cq(f'http://q1.qlogo.cn/g?b=qq&nk={jrxm_l}&s=1')} {getname(jrxm_l)}" for jrxm_l in jrxm_list]
                        result = "\n".join(jrxm_result)
                        if len(data_jrxm['jrxm']) > 5:
                            return f'今日鸽子（们）：\n{result}\n\n今天真是鸽子大军呢...'
                        else:
                            return f'今日鸽子（们）：\n{result}'
                    else:
                        return f'今天还没有小猫呢'
            else:
                return f'世界就是绕着你打转！'   
        else:
            return run.__doc__
    except Exception as e:
        print(traceback.format_exc())
        return run.__doc__