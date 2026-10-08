"""Focused regression tests. Run with the CPU daemon environment."""
import contextlib
import importlib.util
import io
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]

def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / filename)
    m = importlib.util.module_from_spec(spec)
    sys.modules[name] = m
    spec.loader.exec_module(m)
    return m

installer = load('parakeet_installer', 'dusky_installer.py')
trigger = load('parakeet_trigger', 'dusky_trigger.py')
main = load('parakeet_main', 'dusky_main.py')
worker = load('parakeet_worker', 'dusky_worker.py')

class InstallerTests(unittest.TestCase):
    def test_selftest_requires_valid_success_report(self):
        for data in ('', 'not json', '[]', '{"ok":false}', '{"ok":1}'):
            with self.subTest(data=data), patch.object(installer, 'run', return_value=SimpleNamespace(stdout=data)):
                with self.assertRaises(installer.InstallError):
                    installer.verify_worker(Path('/python'), Path('/app'), 'cpu', 0, Path('/config'))

    def test_valid_selftest(self):
        with patch.object(installer, 'run', return_value=SimpleNamespace(stdout='{"ok":true}\n')):
            self.assertTrue(installer.verify_worker(Path('/python'), Path('/app'), 'cpu', 0, Path('/config'))['ok'])

    def test_bad_vram_budget(self):
        for value in (0, -1, 1500):
            with self.subTest(value=value), self.assertRaises(installer.InstallError):
                installer.choose_vram_limit(2048, value)
        self.assertEqual(installer.choose_vram_limit(2048, None), 1280)

    def test_menu_respects_enter_and_rejects_bad_choice(self):
        with patch('builtins.input', return_value=''), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(installer.choose_interactive('cpu', {}), 'cpu')
        with patch('builtins.input', return_value='9'), contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(installer.InstallError):installer.choose_interactive('cpu', {})

    def test_rollback_restores_app_and_entrypoints(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);app=root/'app';old=root/'backup';app.mkdir();old.mkdir()
            (app/'version').write_text('new');(old/'version').write_text('old')
            entry=root/'trigger';entry.write_text('new')
            with patch.object(installer,'APP_DIR',app):
                installer.rollback(old,{entry:(b'old trigger',0o755)},manage_service=False,was_active=False,was_enabled=False)
            self.assertEqual((app/'version').read_text(),'old')
            self.assertEqual(entry.read_text(),'old trigger')
            self.assertFalse(old.exists())

    def test_verbose_commands_stream_and_log(self):
        with tempfile.TemporaryDirectory() as td,patch.object(installer,'LOG_FILE',Path(td)/'install.log'),patch.object(installer,'VERBOSE',True),contextlib.redirect_stdout(io.StringIO()) as out:
            installer.run([sys.executable,'-c','print("streamed output")'],quiet=False)
            self.assertIn('streamed output',out.getvalue())
            self.assertIn('streamed output',installer.LOG_FILE.read_text())

    def test_failed_deployment_restores_running_service(self):
        for failure in (OSError('rename failed'), KeyboardInterrupt()):
            with self.subTest(failure=type(failure).__name__), tempfile.TemporaryDirectory() as td:
                app = Path(td) / 'app'; app.mkdir()
                (app / 'version').write_text('old')
                stage = Path(td) / 'missing-stage'
                rename = Path.rename
                def fail_stage(path, target):
                    if path == stage:
                        raise failure
                    return rename(path, target)
                with patch.object(installer, 'APP_DIR', app), patch.object(Path, 'rename', fail_stage), patch.object(installer.subprocess, 'run', return_value=SimpleNamespace(returncode=0, stdout='', stderr='')) as commands:
                    with self.assertRaises(installer.InstallError if isinstance(failure, Exception) else KeyboardInterrupt):
                        installer.deploy_stage(stage, was_active=True)
                self.assertEqual((app / 'version').read_text(), 'old')
                self.assertIn(['systemctl', '--user', 'start', installer.UNIT_NAME], [call.args[0] for call in commands.call_args_list])

    def test_failed_service_stop_does_not_deploy(self):
        with tempfile.TemporaryDirectory() as td:
            app = Path(td) / 'app'; app.mkdir()
            stage = Path(td) / 'stage'; stage.mkdir()
            (app / 'version').write_text('old')
            with patch.object(installer, 'APP_DIR', app), patch.object(installer.subprocess, 'run', return_value=SimpleNamespace(returncode=1)):
                with self.assertRaisesRegex(installer.InstallError, 'Could not stop'):
                    installer.deploy_stage(stage, was_active=True)
            self.assertEqual((app / 'version').read_text(), 'old')
            self.assertTrue(stage.is_dir())

    def test_custom_app_launcher_exports_runtime_path(self):
        with tempfile.TemporaryDirectory(prefix='app space ') as td:
            root=Path(td);app=root/'app';app.mkdir();(app/'config.json').write_text(json.dumps({'state_dir':str(root/'state')}))
            (app/installer.UNIT_NAME).write_text((ROOT/installer.UNIT_NAME).read_text())
            with patch.object(installer,'APP_DIR',app),patch.object(installer,'BIN_DIR',root/'bin'),patch.object(installer,'UNIT_DIR',root/'units'),patch.object(installer,'run'):
                installer.install_entrypoints(start_service=False)
            self.assertIn('export DUSKY_APP_DIR=',(root/'bin/dusky_trigger').read_text())
            self.assertIn('export DUSKY_APP_DIR=',(root/'bin/dusky_verify').read_text())
            self.assertIn(str(root/'state'),(root/'units'/installer.UNIT_NAME).read_text())

    def test_auto_selects_supported_gpu_among_mixed_devices(self):
        report=SimpleNamespace(returncode=0,stdout='0, GTX 1050, 615.71.09, 2048, 6.1\n1, RTX 3050, 615.71.09, 4096, 8.6\n')
        with patch.object(installer.shutil,'which',return_value='/nvidia-smi'),patch.object(installer.subprocess,'run',return_value=report):
            hardware,info=installer.detect_hardware()
        self.assertEqual(hardware,'nvidia')
        self.assertEqual([g['index'] for g in info['gpus']],[1])

    def test_corrupt_installed_vad_can_use_valid_offline_cache(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);app=root/'app';(app/'models').mkdir(parents=True)
            (app/'models/silero_vad.onnx').write_bytes(b'corrupt')
            cache=root/'cached.onnx';cache.write_bytes(b'valid fixture')
            digest=installer.hashlib.sha256(cache.read_bytes()).hexdigest()
            with patch.object(installer,'APP_DIR',app),patch.object(installer,'VAD_CACHE',cache),patch.object(installer,'SILERO_BYTES',13),patch.object(installer,'SILERO_SHA256',digest),patch.object(installer,'OFFLINE',True):
                self.assertEqual(installer.download_silero(root/'stage',None),digest)
            self.assertEqual((root/'stage/models/silero_vad.onnx').read_bytes(),b'valid fixture')

    def test_source_trigger_is_shipped(self):
        self.assertIn('dusky_trigger.py', installer.REQUIRED_SOURCES)
        self.assertTrue((ROOT/'dusky_trigger.py').is_file())

class RuntimeTests(unittest.TestCase):
    def test_ring_counts_replaced_samples(self):
        ring=main.RingBuffer(4);ring.append(main.np.array([1,2,3],dtype='int16'));ring.append(main.np.arange(6,dtype='int16'))
        self.assertEqual(ring.dropped_samples,5)
        self.assertEqual(ring.read().tolist(),[2,3,4,5])

    def test_spawn_failure_closes_socketpair(self):
        manager=main.WorkerManager({})
        pair=socket.socketpair(socket.AF_UNIX,socket.SOCK_SEQPACKET)
        with patch.object(main.socket,'socketpair',return_value=pair), patch.object(main.subprocess,'Popen',side_effect=OSError('failed')):
            with self.assertRaises(OSError):manager.prewarm()
        self.assertTrue(all(s.fileno()==-1 for s in pair))

    def test_ffmpeg_normal_eof_and_generator_close(self):
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/'fixture.wav'
            subprocess.run(['ffmpeg','-hide_banner','-loglevel','error','-f','lavfi','-i','sine=frequency=440:duration=3','-ar','16000',str(path)],check=True)
            chunks=list(main.decode_file_to_pcm(path,1))
            self.assertEqual(sum(x.size for x in chunks),48000)
            decoded=main.decode_file_to_pcm(path,1);next(decoded);decoded.close()

    def test_ffmpeg_invalid_file(self):
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/'bad.wav';path.write_text('bad')
            with self.assertRaisesRegex(RuntimeError,'ffmpeg failed'):list(main.decode_file_to_pcm(path,1))

    def test_typing_failure_does_not_claim_emission(self):
        typer=main.StableSuffixTyper(0)
        with patch.object(main.subprocess,'run',return_value=SimpleNamespace(returncode=1)):
            typer.update('This must not count as typed.',final=True)
        self.assertTrue(typer.disabled);self.assertEqual(typer.emitted,[])

    def test_live_phrases_have_a_separator(self):
        typer = main.StableSuffixTyper(0)
        with patch.object(main.subprocess, 'run', return_value=SimpleNamespace(returncode=0)) as typing:
            typer.update('First phrase.', final=True)
            typer.reset()
            typer.update('Second phrase.', final=True)
        self.assertEqual(b''.join(call.kwargs['input'] for call in typing.call_args_list), b'First phrase. Second phrase.')

    def test_final_typing_lock_does_not_block_capture(self):
        sess = self.session(vad_onset_seconds=0.032, vad_min_speech_seconds=0, realtime_interval_seconds=0)
        sess.realtime = True
        sess.typer = main.StableSuffixTyper(0)
        sess.vad.probability.return_value = 1.0
        sess.daemon.worker.poll = Mock(return_value={'ok': True, 'text': 'Interim.'})
        sess.daemon.worker.cancel = Mock()
        stream = Mock(); stream.__enter__ = Mock(return_value=stream); stream.__exit__ = Mock(return_value=False)
        reads = 0
        def read(_):
            nonlocal reads
            reads += 1
            if reads == 3:
                sess.stop_event.set()
            return main.np.zeros(512, dtype='int16').tobytes(), False
        stream.read.side_effect = read
        sess._typer_lock.acquire()
        with patch.object(main.sd, 'RawInputStream', return_value=stream):
            capture = threading.Thread(target=sess._capture_loop, daemon=True)
            capture.start()
            try:
                capture.join(timeout=0.5)
                self.assertFalse(capture.is_alive(), 'Capture blocked behind final typing')
                self.assertEqual(sess._final_q.qsize(), 1)
            finally:
                sess._typer_lock.release()
                capture.join(timeout=1)

    def session(self, **config):
        daemon=SimpleNamespace(config={'notifications':False,'output_mode':'clipboard', **config},worker=SimpleNamespace(submit=Mock(return_value='request'),wait_result=Mock()))
        with patch.object(main,'StatefulSileroVad',return_value=Mock()):return main.RecordingSession(daemon,False)

    def test_silence_chunk_is_success_without_retry(self):
        sess=self.session();sess.daemon.worker.wait_result.return_value={'ok':True,'text':''}
        with patch.object(main,'decode_file_to_pcm',return_value=iter([main.np.zeros(16000,dtype='int16')])), patch.object(sess,'_publish',return_value=''):
            sess.run_file(Path('/audio'))
        self.assertEqual(sess.daemon.worker.submit.call_count,1)

    def test_failed_chunk_marks_result_partial(self):
        sess=self.session();sess.daemon.worker.wait_result.return_value={'ok':False,'error':'inference failed'}
        with patch.object(main,'decode_file_to_pcm',return_value=iter([main.np.zeros(16000,dtype='int16')])), patch.object(sess,'_publish',return_value='') as publish:
            with self.assertRaisesRegex(RuntimeError,'Transcription incomplete'):sess.run_file(Path('/audio'))
        self.assertEqual(sess.daemon.worker.submit.call_count,2)
        self.assertFalse(publish.call_args.kwargs['complete'])

    def test_typing_failure_still_copies_transcript(self):
        with tempfile.TemporaryDirectory() as td:
            sess=self.session();sess.config.update(state_dir=td,output_mode='both')
            def run(cmd,**kwargs):
                if cmd[0]=='wtype':raise subprocess.CalledProcessError(1,cmd)
                return SimpleNamespace(returncode=0)
            with patch.object(main.subprocess,'run',side_effect=run) as helper:sess._publish('Saved words.')
            self.assertTrue(any(x.args[0][0]=='wl-copy' for x in helper.call_args_list))
            self.assertTrue(Path(sess.transcript_path).exists())

    def test_file_is_never_typed(self):
        with tempfile.TemporaryDirectory() as td:
            sess=self.session();sess.is_file=True;sess.config.update(state_dir=td,push_type_at_end=True,output_mode='both')
            with patch.object(main.subprocess,'run') as run:sess._publish('A short file transcript.')
            self.assertFalse(any(x.args[0][0]=='wtype' for x in run.call_args_list))
            self.assertTrue(Path(sess.transcript_path).exists())

    def test_quiet_chunk_boundary_and_lossless_decode(self):
        pcm=main.np.full(320000,1000,dtype='int16');pcm[-16000:]=0
        boundary=main.quiet_chunk_boundary(pcm)
        self.assertTrue(304000 < boundary < 320000)
        self.assertEqual(main.quiet_chunk_boundary(main.np.full(320000,1000,dtype='int16')),320000)

    def test_old_nvidia_compute_capability_rejected(self):
        report=SimpleNamespace(returncode=0,stdout='0, 2048, 615.71.09, 6.1\n',stderr='')
        with patch.object(installer.shutil,'which',return_value='/nvidia-smi'),patch.object(installer.subprocess,'run',return_value=report):
            with self.assertRaisesRegex(installer.InstallError,'Turing'):installer.query_nvidia_gpu(0)

    def test_cancel_keeps_real_worker_queue_bounded(self):
        manager=main.WorkerManager({});manager._inflight['pending']=1
        manager.cancel('pending')
        self.assertIn('pending',manager._inflight)
        manager._fail_generation(1,'crashed')
        self.assertNotIn('pending',manager._inflight)
        self.assertNotIn('pending',manager._results)
        self.assertNotIn('pending',manager._discarded)

    def test_timeout_retires_stuck_worker(self):
        manager=main.WorkerManager({});manager._proc=Mock();manager._proc.poll.return_value=None
        manager._inflight['pending']=1
        self.assertIsNone(manager.wait_result('pending',0))
        manager._proc.kill.assert_called_once()

    def test_clipboard_mode_does_not_live_type(self):
        daemon=SimpleNamespace(config={'output_mode':'clipboard'},worker=Mock())
        with patch.object(main,'StatefulSileroVad',return_value=Mock()):sess=main.RecordingSession(daemon,True)
        self.assertIsNone(sess.typer)

    def test_microphone_failure_preserves_completed_phrases(self):
        sess=self.session();sess.phrases.append('Completed words.')
        with patch.object(sess,'_capture_loop',side_effect=RuntimeError('microphone disconnected')),patch.object(sess,'_publish',return_value='') as publish:
            with self.assertRaisesRegex(RuntimeError,'microphone disconnected'):sess.run()
        self.assertEqual(publish.call_args.args[0],'Completed words.')
        self.assertFalse(publish.call_args.kwargs['complete'])

    def test_continuous_capture_preserves_every_sample(self):
        for limit,count in ((1.0,100),(30.0,1000)):
            with self.subTest(limit=limit):
                sess=self.session(vad_onset_seconds=.096,max_phrase_seconds=limit);sess.vad.probability=Mock(return_value=1.0)
                frames=[main.np.full(512,1000+i,dtype='int16') for i in range(count)]
                stream=Mock();stream.__enter__=Mock(return_value=stream);stream.__exit__=Mock(return_value=False)
                offered=[]
                def read(_):
                    frame=frames.pop(0)
                    if not frames:sess.stop_event.set()
                    return frame.tobytes(),False
                stream.read.side_effect=read
                with patch.object(main.sd,'RawInputStream',return_value=stream),patch.object(sess,'_offer_final',side_effect=lambda _,pcm:offered.append(pcm.copy())):
                    sess._capture_loop()
                self.assertTrue(all(x.size <= 480000 for x in offered))
                main.np.testing.assert_array_equal(main.np.concatenate(offered),main.np.repeat(main.np.arange(1000,1000+count,dtype='int16'),512))

    def test_queue_wait_is_cancellable_and_bounded(self):
        manager=main.WorkerManager({'max_inflight_requests':1,'finalize_timeout_seconds':0.01})
        manager._inflight['busy']=1
        stop=threading.Event();stop.set()
        self.assertIsNone(manager.submit(main.np.zeros(1,dtype='int16'),{},force=True,stop=stop))
        with self.assertRaises(TimeoutError):manager.submit(main.np.zeros(1,dtype='int16'),{},force=True)

    def test_decoder_error_preserves_completed_text(self):
        sess=self.session();sess.daemon.worker.wait_result.return_value={'ok':True,'text':'Completed sentence.'}
        def chunks():
            yield main.np.zeros(16000,dtype='int16')
            raise RuntimeError('decode failed')
        with patch.object(main,'decode_file_to_pcm',return_value=chunks()),patch.object(sess,'_publish',return_value='') as publish:
            with self.assertRaisesRegex(RuntimeError,'decode failed'):sess.run_file(Path('/audio'))
        self.assertEqual(publish.call_args.args[0],'Completed sentence.')
        self.assertFalse(publish.call_args.kwargs['complete'])

    def test_final_queue_overflow_does_not_block_capture(self):
        sess=self.session()
        for i in range(9):sess._offer_final(i,main.np.zeros(1,dtype='int16'))
        self.assertEqual(sess._final_q.qsize(),8)
        self.assertTrue(sess.errors)

    def test_memfd_roundtrip_and_large_reply(self):
        pcm=main.np.arange(16000,dtype='int16');fd=main.create_sealed_audio(pcm)
        try:worker.validate_memfd(fd,16000)
        finally:os.close(fd)
        raw,fd=worker.sealed_response({'ok':True,'text':'large '*20000})
        self.assertEqual(json.loads(raw)['payload'],'memfd')
        try:self.assertEqual(len(json.loads(os.pread(fd,os.fstat(fd).st_size,0))['text']),120000)
        finally:os.close(fd)

class TriggerTests(unittest.TestCase):
    def test_wait_uses_exact_job_and_does_not_restart(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);transcripts=root/'transcripts';transcripts.mkdir();jobs=root/'jobs';jobs.mkdir()
            text=transcripts/'correct.txt';text.write_text('Correct result.\n')
            (jobs/'exact.json').write_text(json.dumps({'ok':True,'path':str(text),'job':'exact'}))
            with patch.object(trigger,'transcripts_dir',return_value=transcripts), patch.object(trigger,'send_command',side_effect=AssertionError('must not start service')),contextlib.redirect_stdout(io.StringIO()) as out:
                result=trigger.wait_for_transcript(0,'exact')
            self.assertTrue(result['ok']);self.assertEqual(out.getvalue(),'Correct result.\n')

    def test_wait_reports_failed_exact_job(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);(root/'jobs').mkdir();(root/'jobs/fail.json').write_text('{"ok":false,"error":"missing chunk"}')
            with patch.object(trigger,'transcripts_dir',return_value=root/'transcripts'):
                self.assertFalse(trigger.wait_for_transcript(0,'fail')['ok'])

if __name__=='__main__':unittest.main()
