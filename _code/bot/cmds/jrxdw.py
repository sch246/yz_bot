'''今日小动物'''
import time
import re

from main import storage, getstorage, random, getname, headshot, read_params, cache, cq, pages

prefix: list[str] = storage.get('bbxdw', 'prefix', list)
suffix: list[str] = storage.get('bbxdw', 'suffix', list)
animal_types: dict[str, float] = storage.get('bbxdw', 'animal_types')



happys = [
'＼(＾▽＾)／',
'(≧▽≦)',
'(｡♥‿♥｡)',
'☆*:.｡.o(≧▽≦)o.｡.:*☆',
'(￣▽￣)ノ',
'(๑>ᴗ<๑)',
'＼(＾０＾)／',
'ヽ(＾Д＾)ﾉ',
'(≧◡≦) ♡',
]

def my_zip(*lists):
    if len(lists)==1:
        return ([arg] for arg in lists[0])
    return zip(*lists)

def the_zip(*lists):
    if not lists or not lists[0]:
        return ()
    length = min(len(lst) for lst in lists)
    return ((lst[i] for lst in lists)
            for i in range(length))

re_num = re.compile(r'^(\d+(\.\d+)?)|(\.\d+)$')

def get_jrxdw(personal_animal_types: list[list]|None):
    '''
    获取今日小动物

    Arg:
        personal_animal_types: 个人的今日小动物列表，不填默认全部动物
    '''
    if not personal_animal_types:
        animal_type = random.choice(list(animal_types.keys()))
    else:
        animals, weights = the_zip(*personal_animal_types)
        animal_type = random.choices(animals, weights=weights)[0]
        if animal_type == '*':
            others = list(set(animal_types.keys()) - set(animals))
            if not others:
                animal_type = '虚空生物'
            animal_type = random.choice(others)

    repeat_chance = animal_types.get(animal_type, 0)

    final_animal_type = animal_type
    while random.random() < repeat_chance:
        final_animal_type += animal_type

    if not prefix and not suffix:
        return final_animal_type

    fix_type = random.choices(['prefix', 'suffix'], weights=[len(prefix), len(suffix)])[0]
    if fix_type == 'prefix':
        return random.choice(prefix) + final_animal_type
    else:
        return final_animal_type + random.choice(suffix)


def run(body:str):
    '''今日小动物都是谁呢？
格式:
.jrxdw           # 今日小动物
.jrxdw list      # 获取今日小动物列表
.jrxdw me        # 查看自己的今日小动物类别
.jrxdw set (<animal_type> <weight:num>?)*
    # 设置自己的今日小动物类别，可以用空格隔开，每个类别不能超过4个字，每个权重默认为1.0，每个类别后面能设置权重（可选）
    # 使用 * 设定剩余权重的和，不写默认为0
    # 例如 .jrxdw set 猫 .1 兔 狐 3.14 鸟 6 * 0
.jrxdw set                 # 重置自己的今日小动物类别'''
    if not animal_types:
        return '目前没有小动物，请先.bbxdw add 添加动物类型'


    msg = cache.thismsg()
    qq = str(msg['user_id'])
    is_groupmode = 'group_id' in msg

    sender = getname()

    date = time.strftime('%y-%m-%d')
    is_fool = time.strftime('%m-%d')=='04-01'

    user_data = getstorage()
    personal_animal_types = user_data.get('animal_types') #可能为None

    jrxdw: dict = storage.get('','jrxdw')
    jrxdw.setdefault('dict', {})
    jrxdw_dict: dict = jrxdw['dict']

    number = 0

    # 新的一天，刷新列表
    if not jrxdw.get('date')==date:
        jrxdw['date'] = date
        jrxdw_dict.clear()

    if not body.strip():
        # 随机今日小动物，或者查看
        if not qq in jrxdw_dict:
            # 还没创建
            jrxdw_dict[qq] = get_jrxdw(personal_animal_types)

        number = list(jrxdw_dict.keys()).index(qq) + 1

        if is_groupmode and is_fool:
            # 愚人节
            return f'今日鸽子（第{number}只）是\n{sender}！\n{headshot(qq)}\n今天柚子可以是{jrxdw_dict[qq]}！{random.choice(happys)}'

        if is_groupmode:
            return f'今日小动物（第{number}只）是\n{sender}！\n{headshot(qq)}\n今天你是{jrxdw_dict[qq]}!'
        else:
            return f'今日小动物是\n{sender}！\n{headshot(qq)}\n今天你是{jrxdw_dict[qq]}!'

    s, last = read_params(body)
    if s=='list':
        # 列出小动物
        if not is_groupmode:
            return f'世界就是绕着你打转！'
        elif not jrxdw_dict:
            return '今天还没有小动物呢'

        number = len(jrxdw_dict)

        if is_fool:
            # 愚人节
            result = "\n".join([
                f"{cq.url2cq(f'http://q1.qlogo.cn/g?b=qq&nk={qq}&s=1')} {getname(qq)}"
                        for qq in jrxdw_dict.keys()
            ])
            if len(jrxdw_dict) > 5:
                return f'今日鸽子（们）：\n{result}\n\n今天真是鸽子大军呢...'
            else:
                return f'今日鸽子（们）：\n{result}'

        else:
            result = "\n".join([
                f"{cq.url2cq(f'http://q1.qlogo.cn/g?b=qq&nk={qq}&s=1')}   ↖{jrxdw_dict[qq]}" for qq in jrxdw_dict
            ])
            if len(jrxdw_dict) > 5:
                return f'今日小动物（们）：\n{result}\n\n今天真是小动物大军呢...'
            else:
                return f'今日小动物（们）：\n{result}'
    if s=='me':
        if not personal_animal_types:
            return '你还没有设置自己的小动物！'
        if isinstance(personal_animal_types[0], str):
            personal_animal_types = [[name, 1] for name in personal_animal_types]
        show = "\n".join([f"{k[0]}: {k[1]}" for k in personal_animal_types])
        return f'你的小动物及其权重是：\n{show}'
    if s=='set':
        args = last.strip().split()
        if args:
            add_types = []
            while args:
                head = args.pop(0)
                if re_num.match(head):
                    return run.__doc__
                elif head not in [*animal_types.keys(), '*']:
                    return f'不存在的动物 {head}，请先通过 .bbxdw add 来添加动物'
                if args and re_num.match(args[0]):
                    weight = float(args.pop(0))
                    add_types.append([head, weight])
                else:
                    add_types.append([head, 1])

            user_data['animal_types'] = add_types
            show = "\n".join([f"{k[0]}: {k[1]}" for k in add_types])
            return f'已设置你的小动物及其权重：\n{show}'
        elif user_data.get('animal_types'):
            del user_data['animal_types']
            return '已重置你的小动物设置'
        else:
            return f'无需重置，本就没有设置小动物\n可设置种类:\n{list(animal_types.keys())}'

    else:
        return run.__doc__
