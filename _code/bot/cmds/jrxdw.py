'''今日小动物'''
import time

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


def get_jrxdw(personal_animal_types: list[str]):
    '''
    获取今日小动物

    Arg:
        personal_animal_types: 个人的今日小动物列表，默认全部动物
    '''
    fix_type = random.choices(['prefix', 'suffix'], weights=[len(prefix), len(suffix)])[0]
    animal_type = random.choice(personal_animal_types or list(animal_types.keys()))

    repeat_chance = animal_types.get(animal_type, 0)

    final_animal_type = animal_type
    while random.random() < repeat_chance:
        final_animal_type += animal_type

    if fix_type == 'prefix':
        return random.choice(prefix) + final_animal_type
    else:
        return final_animal_type + random.choice(suffix)


def run(body:str):
    '''今日小动物都是谁呢？
格式:
.jrxdw           # 今日小动物
.jrxdw list      # 获取今日小动物列表
.jrxdw set <animal_type>*  # 设置自己的今日小动物类别，可以用空格隔开，每个类别不能超过4个字
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
    if s=='set':
        personal_animal_types = last.strip().split()
        if personal_animal_types:
            for animal_type in personal_animal_types:
                if not animal_type in animal_types:
                    return f'不存在的动物 {animal_type}，请先通过 .bbxdw add 来添加动物'
            user_data['animal_types'] = personal_animal_types
            return f'已设置动物种类 {personal_animal_types}'
        elif user_data.get('animal_types'):
            del user_data['animal_types']
            return '已重置你的动物种类设置'
        else:
            return f'无需重置，本就没有设置动物种类\n建议种类:\n{list(animal_types.keys())}'

    else:
        return run.__doc__
