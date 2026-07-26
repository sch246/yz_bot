'''交互式文件编辑器'''

import os
import re
import copy
from main import cq, cache, getstorage, is_msg, read_params, storage

# --- 每个文件的单独session ---
sessions = storage.get('edit','sessions')

# --- 正则表达式预编译 ---
PAGE_SIZE_REGEX = re.compile(r"^s\s+(\d+)$", re.IGNORECASE)
PAGE_NAV_REGEX = re.compile(r"^(p|n)\s*(\d*)$", re.IGNORECASE)
JUMP_REGEX = re.compile(r"^(-?\d+)$")
BLOCK_EDIT_HEADER_REGEX = re.compile(r"^file: (.*?)(?:\s*\(unsaved\))? \| line: (\d+) \| size: (\d+)", re.IGNORECASE)
SINGLE_LINE_EDIT_REGEX = re.compile(r"^(\d+)\s*[|│](.*)$")
REST_LINE_REGEX = re.compile(r"^\.\.\.rest \d+ lines?$")

# --- 辅助函数 ---

def _get_path(storage: dict, group_id):
    if group_id:
        storage.setdefault('edits', {})
        return storage.get('edits').get(group_id)
    else:
        return storage.get('edit')

def _set_path(storage: dict, group_id, path: str):
    if group_id:
        storage.setdefault('edits', {})
        storage.get('edits')[group_id] = path
    else:
        storage['edit'] = path

def _get_chat_id(group_id, user_id):
    if group_id:
        return f"group{group_id}"
    else:
        return f"user{user_id}"

def _get_session(path, chat_id):

    # 场景 A: 启动新会话或恢复旧会话

    if os.path.isdir(path):
        return None, f"'{path}' 是一个目录，无法作为文件编辑。"

    if path in sessions:
        return sessions[path], None

    try:
        with open(path, 'r', encoding='utf-8') as f:
            lines = f.read().splitlines()
    except FileNotFoundError:
        # 这是正常情况，代表创建新文件
        lines = []
    except PermissionError:
        return None, f"权限不足，无法读取 '{path}'。"
    except OSError as e:
        # 捕获更广泛的操作系统错误，比如无效的文件名
        return None, f"无法读取文件 '{path}'。原因: {e}"
    except Exception as e:
        # 捕获其他未知错误
        return None, f"读取文件时发生未知错误: {e}"

    session = {
        'owner': chat_id,
        'filepath': path,
        'lines': lines,
        'current_line': 0,
        'page_size': 20,
        'show_linenumbers': True,
        'undo_stack': [],
        'redo_stack': [],
        'is_dirty': False,
        'quit_confirm': False,
        'last_input_was_invalid': False,
    }
    sessions[path] = session
    return session, None

def _strip_index(line: str):
    if m := SINGLE_LINE_EDIT_REGEX.match(line):
        return m.group(2)
    return line

def _render_page(session: dict) -> str:
    """根据会话状态渲染要显示给用户的文本页面"""
    filepath = session['filepath']
    current_line = session['current_line']
    page_size = session['page_size']
    lines = session['lines']
    total_lines = len(lines)

    max_start_line = max(0, total_lines - page_size) if total_lines > page_size else 0
    current_line = max(0, min(current_line, max_start_line))
    session['current_line'] = current_line

    start = current_line
    end = current_line + page_size
    
    page_lines_content = lines[start:end]

    dirty_indicator = " (unsaved)" if session.get('is_dirty') else ""
    header = f"file: {filepath}{dirty_indicator} | line: {current_line} | size: {page_size}"
    
    if session['show_linenumbers']:
        content = [f"{i}│{line}" for i, line in enumerate(page_lines_content, start=start)]
    else:
        content = page_lines_content

    if end < total_lines:
        remaining_lines = total_lines - end
        if remaining_lines == 1:
            content.append("...rest 1 line")
        else:
            content.append(f"...rest {remaining_lines} lines")
    
    return header + '\n' + '\n'.join(content)

def _save_to_disk(filepath: str, lines: list) -> tuple[bool, str]:
    """将内容写入磁盘"""
    try:
        dir_name = os.path.dirname(filepath)
        if dir_name:
            os.makedirs(dir_name, exist_ok=True)
        
        with open(filepath, 'w', encoding='utf-8') as f:
            f.write('\n'.join(lines))
        return True, f"Saved."
    except Exception as e:
        return False, f"Save failed: {e}"

# --- 主命令函数 ---

def run(body: str):
    '''
    交互式文本编辑器
    用法: .edit [文件路径]
    仅当在当前窗口编辑过文件时可以省略路径
    '''
    msg = cache.thismsg()
    user_id = msg['user_id']
    group_id = msg.get('group_id')

    if user_id not in cache.ops:
        if not cache.any_same(msg, r'\.edit'):
            return '权限不足 (一定消息内将不再提醒)'
        return

    first_line = body.splitlines()[0] if body.strip() else ""
    path, _ = read_params(first_line)

    storage = getstorage(user_id)
    if not path:
        path = _get_path(storage, group_id)
    if not path:
        return run.__doc__

    _set_path(storage, group_id, path)

    # 允许同一个群聊的不同用户编辑同一个文件
    chat_id = _get_chat_id(group_id, user_id)
    session, err = _get_session(path, chat_id)
    if not session:
        return f"获取session失败: {err}"
    if session['owner'] != chat_id:
        return f'当前文件正被 {session["owner"]} 编辑中'
    reply = yield _render_page(session)

    # ============================================
    # 场景 B: 交互式会话循环
    # ============================================
    while True:
        if not is_msg(reply):
            # 用户发送的不是消息（如撤回），不清除会话
            reply = yield
            continue

        user_input = reply['message']

        session, err = _get_session(path, chat_id)
        if not session:
            return f"获取session失败: {err}"
        if session['owner'] != chat_id:
            return f'当前文件正被 {session["owner"]} 编辑中'

        is_current_input_valid = True
        cmd = user_input.strip().lower()

        # --- 指令解析 ---
        
        # 在处理任何指令前，如果不是再次确认退出，则重置退出确认标志
        if cmd != 'q':
            session['quit_confirm'] = False

        if cmd == 'q':
            if session['is_dirty'] and not session.get('quit_confirm', False):
                session['quit_confirm'] = True
                reply = yield "有未保存的更改。再次输入 'q' 将不保存并退出。"
                continue
            else:
                del sessions[path]
                return "编辑会话已结束。"

        elif cmd == 'q!':
            del sessions[path]
            return "编辑会话已结束。"
        
        elif cmd == 'wq':
            success, message = _save_to_disk(session['filepath'], session['lines'])
            if success:
                session['is_dirty'] = False
                del sessions[path]
                return message + "\n编辑会话已结束。"
            else:
                reply = yield message + "\n保存失败，会话未退出。" # 提示用户保存失败，但不退出
                continue
        
        elif cmd == 'w':
            success, message = _save_to_disk(session['filepath'], session['lines'])
            if success:
                session['is_dirty'] = False
            reply = yield message
            continue

        elif cmd == 'd':
            # session本身是引用类型
            return "挂起编辑会话。"
        
        elif cmd == 'u':
            if len(session['undo_stack']):
                session['redo_stack'].append(session['lines'][:])
                session['lines'] = session['undo_stack'].pop()
                session['is_dirty'] = True
            reply = yield _render_page(session)
            continue
            
        elif cmd == 'r':
            if session['redo_stack']:
                session['undo_stack'].append(session['lines'][:])
                session['lines'] = session['redo_stack'].pop()
                session['is_dirty'] = True
            reply = yield _render_page(session)
            continue

        elif cmd == 'i':
            session['show_linenumbers'] = not session['show_linenumbers']
            reply = yield _render_page(session)
            continue
            
        elif m := PAGE_SIZE_REGEX.match(cmd):
            new_size = int(m.group(1))
            session['page_size'] = new_size if new_size > 0 else 1
            reply = yield _render_page(session)
            continue
        
        elif m := PAGE_NAV_REGEX.match(cmd):
            direction, pages_to_move = m.group(1), int(m.group(2) or 1)
            move_lines = session['page_size'] * pages_to_move
            session['current_line'] += move_lines if direction == 'n' else -move_lines
            reply = yield _render_page(session)
            continue
        
        elif m := JUMP_REGEX.match(cmd):
            line_num = int(m.group(1))
            if line_num < 0:
                line_num += len(session['lines'])
            session['current_line'] = line_num
            reply = yield _render_page(session)
            continue
        
        # --- 编辑内容解析 ---
        elif (m := BLOCK_EDIT_HEADER_REGEX.match(user_input)):
            parsed_filepath, start_line_str, new_size_str = m.groups()
            
            if os.path.abspath(parsed_filepath) != os.path.abspath(session['filepath']):
                reply = yield f"错误：您正在尝试编辑 '{parsed_filepath}'，但当前会话绑定的是 '{session['filepath']}'。\n请先使用 'q' 退出当前会话。"
            else:
                lines = user_input.splitlines()
                header, new_content_lines = lines[0], lines[1:]
                
                if new_content_lines and REST_LINE_REGEX.match(new_content_lines[-1]):
                    new_content_lines.pop()

                session['undo_stack'].append(session['lines'][:])
                session['redo_stack'].clear()
                
                start_line = session['current_line']
                page_size =  session['page_size']
                session['lines'][start_line : start_line + page_size] = [_strip_index(line) for line in new_content_lines]
                session['is_dirty'] = True

                # 更新视图设置
                session['current_line'] = int(start_line_str)
                session['page_size'] = int(new_size_str)
                reply = yield _render_page(session)

        elif (m := SINGLE_LINE_EDIT_REGEX.match(user_input)):
            line_num, content = int(m.group(1)), m.group(2)

            if line_num >= len(session['lines']):
                # 扩展列表到需要的长度
                session['lines'].extend([''] * (line_num - len(session['lines']) + 1))

            if 0 <= line_num < len(session['lines']):
                session['undo_stack'].append(session['lines'][:])
                session['redo_stack'].clear()
                session['lines'][line_num] = content
                session['is_dirty'] = True
            reply = yield _render_page(session)
        
        else:
            is_current_input_valid = False
            if session.get('last_input_was_invalid', False):
                return "连续两次无效输入，自动挂起编辑会话。"
            else:
                session['last_input_was_invalid'] = True
                help_text = (
    "无效指令。可用指令:\n"
    "q:退出, w:保存, q!:强制退出, wq:保存并退出\n"
    "d:挂起, i:切换行号, s <每页行数:int>:设置页大小\n"
    "p/n [N:int]:上下翻页, <行号:int>:跳转\n"
    "u:撤销, r:重做\n"
    "或直接复制消息/行，修改后重新发送进行编辑。"
                )
                reply = yield help_text
        
        if is_current_input_valid:
            session['last_input_was_invalid'] = False


