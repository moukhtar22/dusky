#!/usr/bin/env python3
"""Profile the real Dusky launcher in a fresh interpreter, without editing it.

Headless by default: compositor callbacks are NOT terminal frames or optical TTI.
--mode terminal runs against the invoking terminal; do not redirect its output.
Both modes retain the launcher's schema options, lazy engine pool and load path.
Root schemas must be benchmarked with the required privileges already supplied.
No pre-load, fallback engine, pilot.pause(), implicit warmup run, or cache flush.
JSON includes individual runs, inclusive diagnostic spans, and environment data.
Private instrumentation is checked against the installed Textual at runtime;
revalidate it when upgrading. Use -X importtime separately for import attribution.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import signal
import statistics
import subprocess
import sys
import tempfile
import time


def positive(value):
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError('must be positive')
    return number


def worker(args):
    import asyncio
    import functools
    import hashlib
    import importlib.machinery
    import platform
    import resource
    import runpy
    import threading

    spans = []
    marks = {}
    failures = []
    switches = []
    current_phase = 'startup'
    app_box = []
    ns = time.perf_counter_ns
    origin = args.origin_ns
    power_limits = {}
    for path in Path('/sys/devices/virtual/powercap').rglob('constraint_*_power_limit_uw'):
        try:
            power_limits[str(path)] = int(path.read_text())
        except OSError:
            pass
    source_hashes = {}
    for path in (Path(__file__), args.launcher, args.schema,
                 args.launcher.parents[1]/'frontend/ui.py'):
        source_hashes[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()

    def mark(name):
        marks.setdefault(name, (ns() - origin) / 1e6)

    def span(kind, name, start, phase=None, **extra):
        spans.append(dict(kind=kind, name=str(name), phase=phase or current_phase,
                          start_ms=(start-origin)/1e6, duration_ms=(ns()-start)/1e6,
                          thread=threading.current_thread().name, **extra))

    # Avoid losing the measurement process to the launcher's sudo re-exec.
    def no_reexec(*_):
        raise RuntimeError('Launcher requested privilege escalation. Run this benchmark under sudo for root schemas.')
    os.execvp = no_reexec

    original_subprocess_run = subprocess.run
    def timed_subprocess(*a, **kw):
        start = ns()
        phase = current_phase
        command = a[0] if a else kw.get('args')
        try:
            result = original_subprocess_run(*a, **kw)
            span('command', command, start, phase=phase, returncode=result.returncode)
            return result
        except Exception as exc:
            span('command', command, start, phase=phase, error=type(exc).__name__)
            raise
    subprocess.run = timed_subprocess

    def instrument(ui):
        from textual.app import App
        if not hasattr(App, 'batch_update') or not hasattr(App, '_display'):
            raise RuntimeError('Textual instrumentation needs revalidation')
        cls = ui.DuskyTUI
        for name in ('__init__', '_load_one_engine_sync', '_populate_option_list'):
            original = getattr(cls, name)
            def wrap(original=original, name=name):
                @functools.wraps(original)
                def timed(self, *a, **kw):
                    start = ns()
                    try:
                        return original(self, *a, **kw)
                    finally:
                        span('method', name, start, argument=str(a[0]) if a and name != '__init__' else None)
                return timed
            setattr(cls, name, wrap())

        original_compose = cls.compose
        def compose(self):
            # Time only execution of next(), excluding Textual work between yields.
            generator = original_compose(self)
            while True:
                start = ns()
                try:
                    widget = next(generator)
                except StopIteration:
                    span('compose_step', 'compose', start)
                    break
                span('compose_step', 'compose', start)
                yield widget
        cls.compose = compose

        original_mount = cls.on_mount
        async def on_mount(self):
            mark('app_mount_enter_ms')
            await original_mount(self)
            mark('app_mount_return_ms')
            self.call_after_refresh(lambda: mark('first_after_refresh_ms'))
        cls.on_mount = on_mount

        original_display = App._display
        def display(self, screen, renderable):
            eligible = renderable is not None and not self._batch_count and self._running and not self._closed
            start = ns()
            result = original_display(self, screen, renderable)
            if eligible:
                mark('first_compositor_callback_ms')
                if not self.is_headless and self._driver is not None:
                    mark('first_driver_flush_return_ms')
                span('display', 'compositor', start)
            return result
        cls._display = display

        original_factory = ui.CustomRichTabWidget._invoke_factory
        def factory(self):
            start = ns()
            phase = current_phase
            node = self
            shown = True
            while node is not None:
                shown = shown and node.display
                node = node.parent
            try:
                return original_factory(self)
            except Exception as exc:
                failures.append(f'{self.id}: {type(exc).__name__}: {exc}')
                raise
            finally:
                span('factory', self.id, start, phase=phase, ancestor_display=shown)
        ui.CustomRichTabWidget._invoke_factory = factory

        # The opt-in collector performs I/O before the UI-thread renderer.
        # Record its failures even when the widget catches them to display an error.
        original_widget_init = ui.CustomRichTabWidget.__init__
        def widget_init(self, *a, **kw):
            original_widget_init(self, *a, **kw)
            original_collector = getattr(self, 'collector', None)
            if original_collector is not None:
                def collect(prepared):
                    start = ns()
                    phase = current_phase
                    try:
                        return original_collector(prepared)
                    except Exception as exc:
                        failures.append(f'{self.id} collector: {type(exc).__name__}: {exc}')
                        raise
                    finally:
                        span('collector', self.id, start, phase=phase)
                self.collector = collect
        ui.CustomRichTabWidget.__init__ = widget_init

        async def wait_until(predicate, description):
            deadline = time.monotonic() + args.timeout
            while not predicate():
                if time.monotonic() >= deadline:
                    raise TimeoutError(description)
                await asyncio.sleep(0.005)

        async def refreshed(app):
            event = asyncio.Event()
            if not app.call_after_refresh(event.set):
                raise RuntimeError('Refresh callback rejected')
            await asyncio.wait_for(event.wait(), args.timeout)

        def ready(app, idx):
            if idx is None:
                return True
            if idx not in app._tab_data_ready or idx not in app._populated_tabs:
                return False
            name = app.tabs[idx]
            if idx in app.custom_views or name in app.custom_views:
                # Custom-only tabs have no schema engine dependencies. Include
                # default backend completion, not just an empty dependency set.
                if app.default_engine_key not in app._loaded_engines:
                    return False
                for view in app.query_one(f'#tab-{idx}').query(ui.CustomRichTabWidget):
                    if view._refresh_inflight or view._last_rendered_repr is None:
                        return False
            return True

        async def observe(app):
            nonlocal current_phase
            initial = app._initial_tab
            await wait_until(lambda: ready(app, initial) or bool(app._failed_engines), 'active data readiness')
            if app._failed_engines:
                raise RuntimeError(f'Backend load failed: {app._failed_engines}')
            await refreshed(app)
            mark('active_data_refresh_ms')
            await wait_until(lambda: app._boot_complete and not getattr(app, '_inventory_refreshing', False), 'global boot/discovery')
            if app._failed_engines:
                raise RuntimeError(f'Backend load failed: {app._failed_engines}')
            await refreshed(app)
            mark('boot_refresh_ms')
            marks['viewport_width'] = app.size.width
            marks['viewport_height'] = app.size.height
            marks['widgets_at_boot'] = len(list(app.query('*')))
            marks['custom_widgets_at_boot'] = len(list(app.query(ui.CustomRichTabWidget)))
            if args.observe:
                current_phase = 'idle_observation'
                await asyncio.sleep(args.observe)
            if app._modal_active():
                switches.append({'skipped': 'Schema startup modal is active; not dismissed by benchmark'})
            else:
                targets = [int(x) for x in args.tabs.split(',')] if args.tabs else [k for k in app.tabs if k != initial]
                for target in targets:
                    if target not in app.tabs or target == initial:
                        raise ValueError(f'Invalid noninitial tab: {target}')
                    for visit in ('first_visit', 'revisit'):
                        current_phase = f'tab_{target}_{visit}'
                        warmed = target in app._populated_tabs and target not in app._tab_dirty
                        pane = app.query_one(f'#tab-{target}')
                        children_before = len(list(pane.children))
                        custom_before = list(pane.query(ui.CustomRichTabWidget))
                        retained_content = bool(custom_before) and all(v._last_rendered_repr is not None for v in custom_before)
                        collection_needed = any(getattr(v, 'collector', None) is not None and v._dirty for v in custom_before)
                        start = ns()
                        app.action_switch_tab(target)
                        await wait_until(lambda: app._current_tab_index() == target, 'tab activation')
                        await refreshed(app)
                        await wait_until(lambda: ready(app, target), 'tab data/factory readiness')
                        await refreshed(app)
                        switches.append(dict(tab=target, visit=visit, warmed_before=warmed,
                                             children_before=children_before, retained_content_before=retained_content,
                                             dirty_collector_before=collection_needed, duration_ms=(ns()-start)/1e6))
                        app.action_switch_tab(initial)
                        await wait_until(lambda: app._current_tab_index() == initial, 'return to initial tab')
                        await refreshed(app)
            current_phase = 'shutdown'
            mark('observation_end_ms')

        original_run = cls.run
        def run(app, *a, **kw):
            app_box.append(app)
            mark('app_run_enter_ms')
            async def autopilot(_pilot):
                try:
                    await observe(app)
                except Exception as exc:
                    failures.append(f'{type(exc).__name__}: {exc}')
                finally:
                    app.exit()
            result = original_run(app, headless=args.mode == 'headless',
                                  size=(args.width, args.height), auto_pilot=autopilot)
            if app._exception:
                raise app._exception
            return result
        cls.run = run

    original_exec = importlib.machinery.SourceFileLoader.exec_module
    def exec_module(loader, module):
        name = module.__name__
        tracked = name in ('textual', 'textual.app', 'python.frontend.ui') or name.startswith('dusky_schema_')
        start = ns()
        original_exec(loader, module)
        if tracked:
            span('import_inclusive', name, start)
        if name == 'python.frontend.ui':
            instrument(module)
    importlib.machinery.SourceFileLoader.exec_module = exec_module

    mark('launcher_enter_ms')
    sys.argv = [str(args.launcher), str(args.schema)]
    runpy.run_path(str(args.launcher), run_name='__main__')
    mark('launcher_return_ms')
    if not app_box:
        raise RuntimeError('Launcher returned without running DuskyTUI')
    usage = resource.getrusage(resource.RUSAGE_SELF)
    process_cpu_ms = time.process_time() * 1000
    # Fingerprint loaded framework sources after the measured launcher returns.
    # Module paths expose accidental imports from a different checkout.
    for name, module in tuple(sys.modules.items()):
        if name.startswith(('python.frontend.', 'python.engines.')):
            filename = getattr(module, '__file__', None)
            if filename:
                path = Path(filename).resolve()
                if path.is_file():
                    source_hashes[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
    import textual
    app = app_box[0]
    metrics = dict(marks)
    for kind in ('factory', 'collector', 'command', 'compose_step'):
        selected = [s for s in spans if s['kind'] == kind and s['phase'] == 'startup']
        metrics[f'startup_{kind}_count'] = len(selected)
        metrics[f'startup_{kind}_inclusive_ms'] = sum(s['duration_ms'] for s in selected)
    metrics['max_rss_kib'] = usage.ru_maxrss
    metrics['process_cpu_ms'] = process_cpu_ms
    output = dict(metrics=metrics, spans=spans, switches=switches, errors=failures,
                  environment=dict(python=sys.version, executable=sys.executable, textual=textual.__version__,
                    kernel=platform.release(), uid=os.geteuid(), home=str(Path.home()),
                    gil_enabled=sys._is_gil_enabled(), cpu_count=os.cpu_count(),
                    affinity=sorted(os.sched_getaffinity(0)), term=os.environ.get('TERM'),
                    term_program=os.environ.get('TERM_PROGRAM'), mode=args.mode,
                    size=[args.width,args.height], pycache_prefix=sys.pycache_prefix,
                    power_limits_uw=power_limits, source_sha256=source_hashes,
                    sync_output_detected=app._sync_available),
                  schema=str(args.schema), launcher=str(args.launcher))
    Path(args.result).write_text(json.dumps(output, indent=2))
    if failures:
        raise RuntimeError('; '.join(failures))


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('schema', type=Path)
    parser.add_argument('--launcher', type=Path, default=Path(__file__).resolve().parents[2]/'main/main.py')
    parser.add_argument('--runs', '-n', type=positive, default=5)
    parser.add_argument('--mode', choices=('headless','terminal'), default='headless')
    parser.add_argument('--width', type=positive, default=120)
    parser.add_argument('--height', type=positive, default=40)
    parser.add_argument('--timeout', type=positive, default=60, help='seconds per child, including shutdown')
    parser.add_argument('--tabs', default='', help='comma-separated noninitial tab IDs (default: all)')
    parser.add_argument('--observe', type=float, default=0, help='idle seconds before switches, to expose hidden timers')
    parser.add_argument('--json', action='store_true')
    parser.add_argument('--output', type=Path, help='save complete JSON report, including raw samples')
    parser.add_argument('--label', default='', help='power/terminal/cache condition supplied by operator')
    parser.add_argument('--worker', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--origin-ns', type=int, default=0, help=argparse.SUPPRESS)
    parser.add_argument('--result', help=argparse.SUPPRESS)
    args = parser.parse_args()
    for name in ('schema','launcher'):
        path = getattr(args, name).expanduser().resolve()
        if not path.is_file():
            parser.error(f'{name} is not a file: {path}')
        setattr(args,name,path)
    if not 0 <= args.observe < args.timeout:
        parser.error('--observe must be nonnegative and less than --timeout')
    if args.mode == 'terminal' and not (sys.stdin.isatty() and sys.stdout.isatty()):
        parser.error('--mode terminal requires a real terminal on stdin and stdout')
    if args.worker:
        worker(args)
        return
    records = []
    with tempfile.TemporaryDirectory(prefix='dusky-benchmark-') as directory:
        for index in range(args.runs):
            result = Path(directory)/f'{index}.json'
            command = [sys.executable, str(Path(__file__).resolve()), str(args.schema),
                       '--launcher', str(args.launcher), '--worker', '--result', str(result),
                       '--mode',args.mode,'--width',str(args.width),'--height',str(args.height),
                       '--timeout',str(args.timeout),'--observe',str(args.observe),'--tabs',args.tabs,
                       '--origin-ns',str(time.perf_counter_ns())]
            # A process group lets timeout cleanup include external read commands.
            proc = subprocess.Popen(command, start_new_session=args.mode == 'headless',
                                    stdout=subprocess.PIPE if args.mode == 'headless' else None,
                                    stderr=subprocess.PIPE if args.mode == 'headless' else None,
                                    text=True)
            try:
                stdout, stderr = proc.communicate(timeout=args.timeout)
            except subprocess.TimeoutExpired:
                if args.mode == 'headless':
                    os.killpg(proc.pid, signal.SIGKILL)
                else:
                    proc.kill()
                proc.communicate()
                raise RuntimeError(f'Run {index+1} exceeded {args.timeout}s')
            if proc.returncode or not result.is_file():
                raise RuntimeError(f'Run {index+1} failed ({proc.returncode}):\n{stderr or ""}\n{stdout or ""}')
            record = json.loads(result.read_text())
            record['stderr'] = stderr or ''
            records.append(record)
    stats = {}
    for key in records[0]['metrics']:
        values = [r['metrics'][key] for r in records]
        stats[key] = dict(median=statistics.median(values), mean=statistics.mean(values),
                          min=min(values), max=max(values))
    report = dict(format_version=2, label=args.label, runs=args.runs, stats=stats, records=records,
                  caveats=['Fresh interpreters; filesystem and bytecode caches are uncontrolled, not cold disk.',
                           'Timestamp milestones overlap: do not add them or inclusive diagnostic spans.',
                           'Headless callbacks do not measure terminal output or optical presentation.',
                           'Driver flush return is not terminal/compositor presentation acknowledgement.',
                           'Active data refresh is a readiness proxy, not proven input-to-pixel TTI.',
                           'First visit may already be warmed; see per-switch metadata.',
                           'CPU/RSS include observation, switches and shutdown.'])
    if args.output:
        args.output.expanduser().write_text(json.dumps(report,indent=2)+'\n')
    if args.json:
        print(json.dumps(report,indent=2))
    else:
        print(f'{args.mode}: {args.runs} fresh processes; {args.width}x{args.height}; {args.label}')
        print('Milestones are ms from parent spawn request (not additive).')
        for name, stat in stats.items():
            print(f'{name:42} median={stat["median"]:9.2f} min={stat["min"]:9.2f} max={stat["max"]:9.2f}')
        print('Tab timings and diagnostic spans are in --output / --json. Headless results do not certify visual artifacts.')


if __name__ == '__main__':
    main()
