import inspect
import httpx
import os
from typing import Any, Callable, List, Dict
from openai import Stream, resources

from openai._client import OpenAIWithRawResponse, OpenAIWithStreamedResponse
from openai.types.chat import ChatCompletionChunk
from openai.types.chat.chat_completion_message import ChatCompletionMessage

from openai.types.chat.chat_completion_chunk import ChoiceDeltaToolCall

from openai import InternalServerError

import json
import traceback
from dotenv import load_dotenv
from tenacity import retry, wait_random_exponential, stop_after_attempt
from termcolor import colored
from openai import OpenAI

from tool import Tool

from main import storage

def dmp(m:ChoiceDeltaToolCall) -> dict[str, Any]:
    return m.model_dump()

class MessageStream:
    def __init__(self, res:Stream[ChatCompletionChunk]) -> None:
        delta = next(res).choices[0].delta
        self._iter = res
        self.role = delta.role
        self.tool_calls = delta.tool_calls
        self.msg = ChatCompletionMessage(
            role=self.role,
            tool_calls=map(dmp, self.tool_calls) if self.tool_calls else None,
            content=None if self.tool_calls else ''
            )
        self.thinking = False

    def __iter__(self):
        return self
    def __next__(self):
        response = next(self._iter)
        if not response.choices:
            prompt_tokens = response.usage.prompt_tokens
            completion_tokens = response.usage.completion_tokens
            return (prompt_tokens, completion_tokens)
        choice = response.choices[0]
        delta = choice.delta
        if self.tool_calls:
            if delta.tool_calls is None:
                raise StopIteration
            delta_text = delta.tool_calls[0].function.arguments
            self.msg.tool_calls[0].function.arguments += delta_text
            return delta_text
        else:
            delta_dict = delta.to_dict(exclude_unset=False)
            if delta_dict.get('reasoning_content'):
                return delta_dict
            elif delta.content:
                self.msg.content += delta.content
                return delta_dict
            else:
                raise StopIteration



role_to_color = {
    "system": "red",
    "user": "green",
    "assistant": "blue",
    "tool": "magenta",
}
def show_args(args):
    return ', '.join([f'{k}={repr(v)}' for k, v in args.items()])
def show_tool_calls(tool_calls):
    return ''.join(map(lambda s:f'\n    {s["function"]["name"]}({show_args(json.loads(s["function"]["arguments"]))})', tool_calls))
def pprint(message:dict | ChatCompletionMessage | MessageStream):
    '''
    打印 dict, 普通消息, 或者流式消息, 然后返回
    流式消息会转换为普通消息
    '''
    if isinstance(message, MessageStream):
        role = message.role
        tool_calls = message.tool_calls
        if message.tool_calls:
            print(colored(f"assistant called: {tool_calls[0].function.name} ", "yellow"),end='', flush=True)
            for delta in message:
                print(colored(delta, "yellow"),end='', flush=True)
        else:
            print(colored(f"assistant: ", role_to_color[role]),end='', flush=True)
            for delta in message:
                print(colored(delta, role_to_color[role]),end='', flush=True)
        print('\n')
        return message.msg
    else:
        if (isinstance(message,ChatCompletionMessage)):
            msg = message.dict()
        else:
            msg = message
        role = msg.get('role')
        tool_calls = msg.get('tool_calls')
        content = msg.get('content')
        name = msg.get('name')
        if role == "system":
            print(colored(f"system: {content}\n", role_to_color[role]))
        elif role == "user":
            pass
            print(colored(f"user: {content}\n", role_to_color[role]))
        elif role == "assistant" and tool_calls:
            print(colored(f"assistant called: {show_tool_calls(tool_calls)}\n", "yellow"))
        elif role == "assistant" and not tool_calls:
            print(colored(f"assistant: {content}\n", role_to_color[role]))
        elif role == "tool":
            print(colored(f"function ({name}): {content}\n", role_to_color[role]))
        else:
            print('else:',msg)
        return message



class Chat(OpenAI):
    completions: resources.Completions
    chat: resources.Chat
    embeddings: resources.Embeddings
    files: resources.Files
    images: resources.Images
    audio: resources.Audio
    moderations: resources.Moderations
    models: resources.Models
    fine_tuning: resources.FineTuning
    beta: resources.Beta
    with_raw_response: OpenAIWithRawResponse
    with_streaming_response: OpenAIWithStreamedResponse

    # client options
    api_key: str
    organization: str | None

    model: str
    tools: Dict[str, Tool]
    messages: List[Dict]
    settings: List[Dict]
    def __init__(self,
            model: str = "gpt-4-0125-preview",
            settings:List[str|Callable]=[],
            api_key: str | None = None,
            base_url: str | httpx.URL | None = None,
            **kws,
            ) -> None:
        load_dotenv()  # load environment variables from .env file

        # 获取AI配置
        ai_config = storage.get('', 'ai_config')
        ai_config.setdefault('default', {
            'api_key': 'OPENAI_API_KEY',
            'base_url': 'OPENAI_BASE_URL',
            'vision': False,
            'func': False,
        })

        # 获取当前模型的配置，如果没有则使用默认配置
        self.model_config = ai_config.get(model, ai_config['default'])
        # 使用环境变量中的实际值
        if api_key is None:
            api_key = os.getenv(self.model_config['api_key'])
        if base_url is None:
            base_url = os.getenv(self.model_config['base_url'])

        # print('init:当前url:', base_url)
        # print('init:当前key:', api_key)

        super().__init__(api_key=api_key,base_url=base_url,**kws)

        self.model = model
        self.tools = {}
        self.messages = []

        self.set_settings(settings)

    def set_settings(self, settings:List[str|Callable]=[]):
        """
        set settings for AI

        :param settings: The setting string described in natural language.
        :return: self
        """
        # for setting in settings:
        #     if isinstance(setting, str):
        #         pprint({'role':'system','content':setting})
        self.settings = settings
        return self

    def add_tool(self, call: Callable, name: str = None) -> None:
        """
        Add a callable tool to the chat system.

        :param call: The callable tool to be added.
        :param name: The name of the tool. Defaults to the function name if not provided.

        加入的函数必须有符合条件的注释，并且做好类型标记，可选参数会被检测出来

        支持的类型请查看 Tool.types

        它本质是 set tool 而不是 add tool ，同样名字的却不同功能的函数请设定不同的 name

        例:

        def get_current_weather(location:str, format:str='celsius'):
            '''
            Get the current weather

            location: The city and state, e.g. San Francisco, CA

            format: The temperature unit to use. Infer this from the users location.
                enum: ["celsius", "fahrenheit"]
            '''
        """
        name = name if name is not None else call.__name__
        self.tools[name] = Tool(call, name)

    def add(self, msg: dict | MessageStream) -> dict | ChatCompletionMessage:
        """
        Print, transform and add a message to the chat history, then return the transformed message
        流式消息会被转换为普通消息

        :param msg: The message to be added.
        :return: The added message.
        """
        msg = pprint(msg)
        self.messages.append(msg)
        return msg

    def call(self, message: dict = None, tool_choice: str = "auto", model: str = None) -> ChatCompletionMessage:
        """
        Process a message, make tool calls if necessary, and return the response message.

        :param message: The message to process.
        :param tool_choice: The tool choice mode.
        :param model: The model to use, defaults to the class model.
        :return: The response message.
        """
        tools = [v.description for v in self.tools.values()]
        model = model if model is not None else self.model
        if message is not None:
            self.add(message)
        stream = self.req(tools, tool_choice, model)
        try:
            res_msg = self.add(stream)
            tool_calls = res_msg.tool_calls
        except KeyboardInterrupt:
            if stream.tool_calls:
                print('\ncanceled\n')
                return {}
            else:
                print('\n')
                self.messages.append(stream.msg)
                return stream.msg
        while tool_calls:
            for tool_call in tool_calls:
                function_name = tool_call.function.name
                try:
                    content = self.tools[function_name].call(**json.loads(tool_call.function.arguments))
                except:
                    content =f'called: {json.loads(tool_call.function.arguments)}\n\n'+ traceback.format_exc()
                self.add({
                    "role": "tool",
                    "name": function_name,
                    "content": content,
                    "tool_call_id": tool_call.id,
                })
            stream = self.req(tools, tool_choice, model)
            try:
                res_msg = self.add(stream)
            except KeyboardInterrupt:
                print('\n')
                self.messages.append(stream.msg)
                res_msg = stream.msg
            tool_calls = res_msg.tool_calls
        return res_msg

    # @retry(wait=wait_random_exponential(multiplier=1, max=40), stop=stop_after_attempt(3))
    def req(self, tools: List[str] = None, tool_choice: str = 'auto', model: str = None) -> MessageStream:
        """
        Make a request to the chat model with streaming.

        :param tools: The list of tools available.
        :param tool_choice: The tool choice mode.
        :param model: The model to use, defaults to the class model.
        :return: The chat Stream.
        """
        messages = []
        for s in self.settings:
            if isinstance(s, Callable):
                s = s(self)
            if isinstance(s, str):
                s = {'role':'system','content':s}
            if isinstance(s, dict) or isinstance(s, ChatCompletionMessage):
                pprint(s)
                messages.append(s)
        messages += self.messages
        for s in self.messages:
            if isinstance(s, dict) or isinstance(s, ChatCompletionMessage):
                pprint(s)
        tools = tools if tools is not None else [v.description for v in self.tools.values()]
        model = model if model is not None else self.model
        try:
            # print('当前url:', self.base_url)
            # print('当前模型:', self.model)
            if tools and self.model_config.get('func'):
                return MessageStream(self.chat.completions.create(
                    model=model,
                    messages=messages,
                    tools=tools,
                    tool_choice=tool_choice,
                    stream=True,  # 开启流式响应
                    stream_options={
                        "include_usage": True,
                    },
                ))
            else:
                return MessageStream(self.chat.completions.create(
                    model=model,
                    messages=messages,
                    stream=True,
                    stream_options={
                        "include_usage": True,
                    },
                ))
        except InternalServerError as e:
            return e.message #最终会成为system消息和#开头的消息


    def create_image(self, prompt:str, size:str, quality:str):
        '''
        Create an image based on the description and return the url
        Square, standard quality images are the fastest to generate.

        @param
        prompt: Description text used to create the image
        size: Picture size
            enum: ["1024x1024", "1024x1792", "1792x1024"]
        quality: Image quality
            enum: ["standard", "hd"]
        '''
        response = self.images.generate(
            model="dall-e-3",
            prompt=prompt,
            size=size,
            quality=quality,
            n=1,
        )

        image_url = response.data[0].url
        return image_url

    def read_image(self, url:str, detail:str='auto'):
        '''
        Read information from the image

        @param
        url: Image url
        detail: control over how the model processes the image and generates its textual understanding
            enum: ["low", "high", "auto"]
        '''
        response = self.chat.completions.create(
        model="gpt-4o",
        messages=[
            {
            "role": "user",
            "content": [
                {"type": "text", "text": "Describe the image in detail"},
                {
                "type": "image_url",
                "image_url": {
                    "url": url,
                    "detail": detail,
                },
                },
            ],
            }
        ]
        )

        return response.choices[0].message.content

if __name__=='__main__':
    chat = Chat(model='gpt-3.5-turbo')
    chat.set_settings(["Don't make assumptions about what values to plug into functions. Ask for clarification if a user request is ambiguous."])
    chat.add({'role':'user','content':'awa'})
    print(chat.add(chat.req()).content)
    print(chat.call({'role':'user','content':'nothing.'}).content)

