
import re
from typing import Any, Callable
import traceback


class CommandManager:
    def __init__(self):
        self.commands = {}
        self.docs = {}
        @self.register('help')
        def _() -> str:
            '''
            显示所有可用的命令及其说明
            '''
            return self.help()

    def register(self, pattern: str):
        def decorator(func: Callable):
            self.commands[pattern] = func
            self.docs[pattern] = func.__doc__
            return func
        return decorator

    def help(self) -> str:
        help_text = []
        for pattern in self.commands:
            help_text.append(pattern)
            if self.docs[pattern]:
                doc = self.docs[pattern].strip()
                help_text.append("    " + "\n    ".join(doc.split("\n")))
        return "\n".join(help_text)

    def _parse_pattern(self, pattern: str) -> str:
        def replace(match):
            name, type_ = match.groups()
            if type_ == 'str':
                return f'(?P<{name}>("[^"]*"|\'[^\']*\'|\\S+))'
            elif type_ == 'int':
                return f'(?P<{name}>-?\\d+)'
            else:  # dict or any other type
                return f'(?P<{name}>[\S\s]+)'
        return re.sub(r'<(\w+):(\w+)>', replace, pattern)

    def _convert_type(self, value: str, type_: str):
        if type_ == 'str':
            if value.startswith('"') or value.startswith("'"):
                return eval(value)
            return value
        elif type_ == 'int':
            return int(value)
        else:  # dict or any other type
            return eval(value)

    def check(self, text: str) -> Callable | None:
        for pattern, func in self.commands.items():
            regex_pattern = self._parse_pattern(pattern)
            match = re.match(f'^{regex_pattern}$', text, re.DOTALL)
            if match:
                arg_spec = re.findall(r'<(\w+):(\w+)>', pattern)
                args = {}
                cont = False
                for name, type_ in arg_spec:
                    value = match.group(name)
                    # print(name, type_, value)
                    try:
                        args[name] = self._convert_type(value, type_)
                    except (ValueError, SyntaxError):
                        # If conversion fails, this command doesn't match
                        # print(traceback.format_exc())
                        cont = True
                        break
                if cont:
                    continue
                return lambda: func(**args)
        return None

    def execute(self, text: str) -> Any:
        command = self.check(text)
        if command:
            return command()
        else:
            raise ValueError(f"No matching command found for: {text}")

