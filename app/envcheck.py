from typing import List, Optional
import os


def require(names: List[str]) -> None:
    if names is None:
        raise RuntimeError('envcheck.require: names is None')
    if not isinstance(names, list):
        raise RuntimeError(f'envcheck.require: names is not list: {type(names)}')

    missing = []
    for name in names:
        if not isinstance(name, str) or len(name) == 0:
            raise RuntimeError(f'envcheck.require: bad env name: {name}')
        if name not in os.environ:
            missing.append(name)
            continue
        value = str(os.environ[name])
        if len(value) == 0:
            missing.append(name)

    if len(missing) > 0:
        raise RuntimeError(f'envcheck.require: missing required env: {", ".join(missing)}')


def get(name: str) -> str:
    if not isinstance(name, str) or len(name) == 0:
        raise RuntimeError('envcheck.get: name is empty')
    if name not in os.environ:
        raise RuntimeError(f'envcheck.get: {name} is not set')

    value = str(os.environ[name])
    if len(value) == 0:
        raise RuntimeError(f'envcheck.get: {name} is empty')
    return value


def get_default(name: str, default_value: str) -> str:
    if not isinstance(name, str) or len(name) == 0:
        raise RuntimeError('envcheck.get_default: name is empty')
    if not isinstance(default_value, str):
        raise RuntimeError(f'envcheck.get_default: default_value is not str: {type(default_value)}')

    if name in os.environ:
        value = str(os.environ[name])
        if len(value) == 0:
            raise RuntimeError(f'envcheck.get_default: {name} is empty')
        return value
    return str(default_value)


def optional(name: str) -> Optional[str]:
    if not isinstance(name, str) or len(name) == 0:
        raise RuntimeError('envcheck.optional: name is empty')
    if name not in os.environ:
        return None

    value = str(os.environ[name])
    if len(value) == 0:
        raise RuntimeError(f'envcheck.optional: {name} is empty')
    return value
