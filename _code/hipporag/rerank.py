import json
import difflib
from pydantic import BaseModel, Field, TypeAdapter
from openai import OpenAI
from copy import deepcopy
from typing import Union, Optional, List, Dict, Any, Tuple, Literal
import re
import ast
from .prompts.filter_default_prompt import best_dspy_prompt

class Fact(BaseModel):
    fact: list[list[str]] = Field(description="事实列表，每个事实是一个包含3个字符串的列表：[主语, 谓语, 宾语]")


class DSPyFilter:
    def __init__(self, hipporag):
        """
        使用处理输入和输出消息所需的配置和模板初始化对象。

        参数:
        hipporag : 提供全局配置和推理所需LLM模型的对象。

        属性:
        dspy_file_path : 全局配置中指定的重排序文件路径。
        one_input_template : 用于格式化输入消息的字符串模板，其中包含特定字段的占位符。
        one_output_template : 用于格式化输出消息的字符串模板，包含特定字段。
        message_template : 使用指定的dspy文件路径生成的模板。
        llm_infer_fn : 使用提供的LLM模型进行推理的函数引用。
        model_name : 全局配置中指定的语言模型名称。
        default_gen_kwargs : 用于存储默认生成关键字参数的字典。
        """
        dspy_file_path = hipporag.global_config.rerank_dspy_file_path
        self.one_input_template = """[[ ## question ## ]]\n{question}\n\n[[ ## fact_before_filter ## ]]\n{fact_before_filter}\n\nRespond with the corresponding output fields, starting with the field `[[ ## fact_after_filter ## ]]` (must be formatted as a valid Python Fact), and then ending with the marker for `[[ ## completed ## ]]`."""
        self.one_output_template = """[[ ## fact_after_filter ## ]]\n{fact_after_filter}\n\n[[ ## completed ## ]]"""
        self.message_template = self.make_template(dspy_file_path)
        self.llm_infer_fn = hipporag.llm_model.infer
        self.model_name = hipporag.global_config.llm_name
        self.default_gen_kwargs = {}

    def make_template(self, dspy_file_path):
        if dspy_file_path is not None:
            dspy_saved = json.load(open(dspy_file_path, 'r'))
        else:
            dspy_saved = best_dspy_prompt

        system_prompt = dspy_saved['prog']['system']
        message_template = [
            {"role": "system", "content": system_prompt},
        ]
        demos = dspy_saved["prog"]["demos"]
        for demo in demos:
            message_template.append({"role": "user", "content": self.one_input_template.format(question=demo["question"], fact_before_filter=demo["fact_before_filter"])})
            message_template.append({"role": "assistant", "content": self.one_output_template.format(fact_after_filter=demo["fact_after_filter"])})
        return message_template

    def parse_filter(self, response):
        sections = [(None, [])]
        field_header_pattern = re.compile('\\[\\[ ## (\\w+) ## \\]\\]')
        for line in response.splitlines():
            match = field_header_pattern.match(line.strip())
            if match:
                sections.append((match.group(1), []))
            else:
                sections[-1][1].append(line)

        sections = [(k, "\n".join(v).strip()) for k, v in sections]
        parsed = []
        for k, value in sections:
            if k == "fact_after_filter":
                try:
                    # 尝试多种解析方法
                    try:
                        # 尝试JSON解析
                        parsed_value = json.loads(value)
                    except json.JSONDecodeError:
                        try:
                            # 尝试Python字面量解析
                            parsed_value = ast.literal_eval(value)
                        except (ValueError, SyntaxError):
                            # 尝试手动解析格式
                            if "fact" in value and "[" in value:
                                # 尝试提取fact数组部分
                                match = re.search(r'"fact":\s*(\[.*?\])', value.replace("\n", ""), re.DOTALL)
                                if match:
                                    try:
                                        facts_str = match.group(1)
                                        parsed_value = {"fact": json.loads(facts_str)}
                                    except:
                                        # 最后的兜底，返回原始值
                                        parsed_value = {"fact": []}
                                else:
                                    parsed_value = {"fact": []}
                            else:
                                parsed_value = {"fact": []}
                    
                    # 验证并标准化结果
                    try:
                        parsed = TypeAdapter(Fact).validate_python(parsed_value).fact
                    except Exception as e:
                        print(f"验证Fact对象失败: {e}, 尝试手动构建")
                        # 兜底方案：如果parsed_value包含fact键，但验证失败，尝试手动构建
                        if isinstance(parsed_value, dict) and "fact" in parsed_value:
                            facts = parsed_value["fact"]
                            if isinstance(facts, list):
                                # 确保每个事实都是包含3个元素的列表
                                valid_facts = []
                                for fact in facts:
                                    if isinstance(fact, list) and len(fact) == 3:
                                        # 确保所有元素都是字符串
                                        valid_facts.append([str(item) for item in fact])
                                parsed = valid_facts
                            else:
                                parsed = []
                        else:
                            parsed = []
                except Exception as e:
                    print(
                        f"解析字段 {k} 时出错: {e}.\n\n\t\t尝试解析值时\n```\n{value}\n```"
                    )
                    parsed = []  # 出错时返回空列表而不是抛出异常

        return parsed

    def llm_call(self, question, fact_before_filter):
        # 构建提示
        messages = deepcopy(self.message_template)
        messages.append({"role": "user", "content": self.one_input_template.format(question=question, fact_before_filter=fact_before_filter)})
        # 调用openai

        self.default_gen_kwargs['max_completion_tokens'] = 512

        response = self.llm_infer_fn(
            messages=messages,
            model=self.model_name,
            **self.default_gen_kwargs
        )

        if len(response) > 1:
            return response[0]
        return response

    def __call__(self, *args, **kwargs):
        return self.rerank(*args, **kwargs)

    def rerank(self,
               query: str,
               candidate_items: List[Tuple],
               candidate_indices: List[int],
               len_after_rerank: int =None) -> Tuple[List[int], List[Tuple], dict]:
        fact_before_filter = {"fact": [list(candidate_item) for candidate_item in candidate_items]}
        try:
            # prediction = self.program(question=query, fact_before_filter=json.dumps(fact_before_filter))
            response = self.llm_call(query, json.dumps(fact_before_filter))
            generated_facts = self.parse_filter(response)
        except Exception as e:
            print('异常', e)
            generated_facts = []
        result_indices = []
        for generated_fact in generated_facts:
            closest_matched_fact = difflib.get_close_matches(str(generated_fact), [str(i) for i in candidate_items], n=1, cutoff=0.0)[0]
            try:
                result_indices.append(candidate_items.index(eval(closest_matched_fact)))
            except Exception as e:
                print('result_indices 异常', e)

        sorted_candidate_indices = [candidate_indices[i] for i in result_indices]
        sorted_candidate_items = [candidate_items[i] for i in result_indices]
        return sorted_candidate_indices[:len_after_rerank], sorted_candidate_items[:len_after_rerank], {'confidence': None}