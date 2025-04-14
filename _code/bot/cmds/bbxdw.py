'''更新百变小动物'''
import re
import os

from main import storage, read_params, pages, str_tool, sendmsg

prefix: list[str] = storage.get('bbxdw', 'prefix', list)
suffix: list[str] = storage.get('bbxdw', 'suffix', list)
animal_types: dict[str, float] = storage.get('bbxdw', 'animal_types')


def run(body:str):
    '''更新百变小动物
格式:
.bbxdw  # 更新小动物
.bbxdw add <animal_type>  #添加小动物类别
.bbxdw set <animal_type> <repeat_chance:float>  #设置叠词概率(0.0~1.0)
.bbxdw del <animal_type>  #删除小动物类别
.bbxdw list  #列出小动物类别
'''
    s, last = read_params(body)

    if s=='add':
        animal_type, last = read_params(last)
        if animal_type:
            if animal_type in animal_types:
                return f'{animal_type} 已存在'
            else:
                animal_types[animal_type] = 0
                return f'已添加 {animal_type}'
    elif s=='set':
        animal_type, last = read_params(last)
        if animal_type:
            if animal_type not in animal_types:
                animal_types[animal_type] = 0
                sendmsg(f'{animal_type} 不存在，已创建')
            repeat_chance, last = read_params(last)
            if str_tool.is_num(repeat_chance):
                repeat_chance = float(repeat_chance)
                if not (0 <= repeat_chance < 1):
                    return '叠词概率必须在0~1内，且不能等于1'
                animal_types[animal_type] = repeat_chance
                return f'设置成功: {animal_type} {repeat_chance}'
    elif s=='del':
        animal_type, last = read_params(last)
        if animal_type:
            if animal_type not in animal_types:
                return f'{animal_type} 不存在'
            else:
                del animal_types[animal_type]
                return f'已删除 {animal_type}'
    elif s=='list':
        if not animal_types:
            return '目前没有小动物'
        return pages.display(
            [f'{k}: {v}' if v > 0 else k
                    for k, v in animal_types.items()],
            page_size=20
        )
    elif not body.strip():
        if not animal_types:
            return '目前没有小动物'
        animalanimal_types = [f'{x}+' if len(x)==1 else x for x in animal_types]
        animal_types_pattern = '|'.join(animalanimal_types)
        animal_types_pattern = f'(?:{animal_types_pattern})'

        # 正则表达式匹配“我是...<动物>”，中间不超过16个字符，允许叠词
        pattern1 = re.compile(rf'我是(.{{0,16}}?){animal_types_pattern}')

        # 正则表达式匹配“我是<动物>”之后不超过9个字符的内容，允许叠词
        pattern2 = re.compile(rf'我是{animal_types_pattern}(.{{0,9}})')

        # 结果集
        results1 = []
        results2 = []

        # 递归遍历指定目录
        for root, dirs, files in os.walk('chatlog/group/0/'):
            for file in files:
                # 拼接完整的文件路径
                path = os.path.join(root, file)
                # 打开并读取文件内容
                with open(path, 'r', encoding='utf-8') as f:
                    content = f.read()
                    # 找到所有匹配项
                    matches1 = pattern1.findall(content)
                    matches2 = pattern2.findall(content)
                    # 过滤掉超过16个字符的匹配项以及不合适的匹配项
                    matches1 = [match.strip() for match in matches1 if len(match) <= 16 and '柚子' not in match and '狐' not in match and '”，改成“' not in match and '怎么把' not in match and '小豆猫' not in match and '。' not in match and '那新猫' not in match]
                    # 过滤掉超过5个字符的匹配项
                    matches2 = [match.strip() for match in matches2 if len(match) <= 5]
                    # 分别添加到对应的结果集
                    results1.extend(matches1)
                    results2.extend(matches2)

        # 去重并排序
        prefix[:] = list(sorted(set(results1)))
        suffix[:] = list(sorted(set(results2)))

        return f'百变小动物已更新'

    return run.__doc__
