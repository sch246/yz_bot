import os
import re
import json
from typing import List, Dict
from dotenv import load_dotenv
from hipporag import HippoRAG
from hipporag.utils.config_utils import BaseConfig
import time
from datetime import datetime
import tiktoken


# 图片正则表达式
image_pattern = re.compile(r'\[CQ:image[^\]]*\]')

encoding = tiktoken.encoding_for_model('gpt-4')

def count_tokens(text:str):
    return len(encoding.encode(text))

def process_message_chunk(messages: List[Dict], type_: str, uid: str, hipporag: HippoRAG) -> None:
    """处理一组消息并存入RAG系统"""
    my_name = "柚子"  # 你的机器人名称
    
    # 构建文档
    if type_ == 'group':
        doc = f"群聊{uid}内的对话记录：\n"
    else:
        doc = f"{my_name}与用户{uid}的私聊记录：\n"
    
    # 处理每条消息
    for msg in messages:
        sender_name = msg.get('sender_name', '未知用户')
        sender_id = msg.get('sender_id', '0')
        content = msg.get('content', '')
        
        # 处理图片
        content = image_pattern.sub('[图片]', content)
        
        if sender_id == 'bot':
            doc += f"{my_name}说：{content}\n"
        else:
            doc += f"{sender_name}({sender_id})说：{content}\n"
    
    # 添加时间戳
    doc += f"时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
    
    # 存入RAG系统
    hipporag.index(docs=[doc])
    print(f"已将一组消息存入RAG系统: {type_} {uid}")

def parse_log_line(line: str) -> Dict:
    """解析日志行"""
    try:
        # 提取发送者信息
        sender_match = re.match(r'【(.*)】(.*?)\((.*?)\)(.*?)\|', line)
        if not sender_match:
            return None
        
        title, name, uid, remaining = sender_match.groups()
        
        # 提取消息内容
        content = line.split('|', 1)[1].strip() if '|' in line else ''
        
        return {
            'sender_name': name,
            'sender_id': uid,
            'content': content,
            'title': title
        }
    except Exception as e:
        print(f"解析行失败: {line}")
        print(f"错误: {str(e)}")
        return None

def process_log_file(file_path: str, type_: str, uid: str, hipporag: HippoRAG):
    """处理单个日志文件"""
    messages = []
    total_tokens = 0
    
    with open(file_path, 'r', encoding='utf-8') as f:
        for line in f:
            if not line.strip() or line.startswith(':'):
                continue
            
            if line.startswith('【'):
                messages.append(parse_log_line(line))
            elif line.startswith('    '):
                if not messages[-1]['content']:
                    messages[-1]['content'] = line.lstrip('    ')
                else:
                    messages[-1]['content'] += '\n' + line.lstrip('    ')


        for msg in messages:
            current_tokens = count_tokens(msg['content'])
            
            # 如果当前消息会导致超过token限制，先处理已有消息
            if total_tokens + current_tokens > 500:
                process_message_chunk(messages, type_, uid, hipporag)
                
    
    # 处理剩余消息
    if messages:
        process_message_chunk(messages, type_, uid, hipporag)

def main():
    # 加载环境变量
    load_dotenv()
    
    # 准备配置
    config = BaseConfig(
        llm_base_url=os.getenv("LLM_BASE_URL"),
        llm_name=os.getenv("LLM_NAME"),
        llm_api_key=os.getenv("LLM_API_KEY"),
        embedding_base_url=os.getenv("EMBEDDING_API_BASE"),
        embedding_api_key=os.getenv("EMBEDDING_API_KEY"),
        embedding_model_name=os.getenv("EMBEDDING_MODEL_NAME"),
        embedding_batch_size=16,
        graph_type="facts_and_sim_passage_node_unidirectional",
        max_new_tokens=4096,
        openie_mode="online"
    )
    
    # 初始化HippoRAG
    hipporag = HippoRAG(global_config=config)
    
    chatlog_dir = "chatlog"
    
    # 处理群聊记录
    group_dir = os.path.join(chatlog_dir, "group")
    if os.path.exists(group_dir):
        for group_id in os.listdir(group_dir):
            group_path = os.path.join(group_dir, group_id)
            if os.path.isdir(group_path):
                for year_month in os.listdir(group_path):
                    month_path = os.path.join(group_path, year_month)
                    if os.path.isdir(month_path):
                        for log_file in os.listdir(month_path):
                            if log_file.endswith('.log'):
                                file_path = os.path.join(month_path, log_file)
                                print(f"处理群聊日志: {file_path}")
                                process_log_file(file_path, 'group', group_id, hipporag)

    # 处理私聊记录
    private_dir = os.path.join(chatlog_dir, "private")
    if os.path.exists(private_dir):
        for user_id in os.listdir(private_dir):
            user_path = os.path.join(private_dir, user_id)
            if os.path.isdir(user_path):
                for year_month in os.listdir(user_path):
                    month_path = os.path.join(user_path, year_month)
                    if os.path.isdir(month_path):
                        for log_file in os.listdir(month_path):
                            if log_file.endswith('.log'):
                                file_path = os.path.join(month_path, log_file)
                                print(f"处理私聊日志: {file_path}")
                                process_log_file(file_path, 'private', user_id, hipporag)

if __name__ == "__main__":
    main() 