"""Root-free harness smoke workload, using the real launcher and INI engine.

Temporary configuration only; its synthetic collector is not a speed benchmark.
"""
from tempfile import TemporaryDirectory
from pathlib import Path
from time import sleep

from rich.text import Text
from python.frontend.core_types import ConfigItem

_directory = TemporaryDirectory(prefix='dusky-benchmark-fixture-')
TARGET_FILE = str(Path(_directory.name) / 'settings.ini')
Path(TARGET_FILE).write_text('[main]\nfirst=1\nsecond=2\n')
ENGINE_TYPE = 'ini'
REQUIRE_ROOT = False
ENABLE_USER_PRESETS = False
APP_TITLE = 'Dusky benchmark smoke fixture'
TABS = ['Initial', 'Options', 'Collector']
SCHEMA = {
    0: [ConfigItem(label='First', key='first', scope='main', type_='int', default=1)],
    1: [ConfigItem(label='Second', key='second', scope='main', type_='int', default=2)],
    2: [],
}


def prepare(app):
    return app.title


def collect(title):
    sleep(0.05)  # Exercise the asynchronous readiness path, not system I/O.
    return title


def render(title):
    return Text(f'Collector complete: {title}')


CUSTOM_VIEWS = {2: {'prepare': prepare, 'collect': collect, 'view': render, 'interval': 0.2}}
