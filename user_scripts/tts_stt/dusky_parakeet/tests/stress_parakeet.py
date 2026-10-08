"""Real-model stress harness; isolates sockets, app files and transcript state.
Run with the installed CPU daemon Python: stress_parakeet.py /path/to/app.
"""
import importlib.util,json,os,re,signal,socket,subprocess,sys,tempfile,time,wave
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]

def request(path,payload):
    with socket.socket(socket.AF_UNIX,socket.SOCK_SEQPACKET) as sock:
        sock.settimeout(5);sock.connect(str(path));sock.send(json.dumps(payload).encode())
        return json.loads(sock.recv(1<<20))

def serve(config):
    spec=importlib.util.spec_from_file_location('stress_main',ROOT/'dusky_main.py')
    main=importlib.util.module_from_spec(spec);sys.modules[spec.name]=main;spec.loader.exec_module(main)
    main.APP_DIR=config.parent
    main.unit_is_enabled=lambda:False
    main.DuskyDaemon._maybe_self_stop=lambda self:None # Never stop the user's service.
    return main.DuskyDaemon(config).run()

def stress(app,audio,repeats):
    with tempfile.TemporaryDirectory(prefix='parakeet-stress-') as td:
        root=Path(td);cfg=json.loads((app/'config.json').read_text())
        cfg.update(state_dir=str(root/'state'),notifications=False,output_mode='clipboard',push_type_at_end=False,
                   worker_python=str(app/'.venv-worker/bin/python'),worker_script=str(ROOT/'dusky_worker.py'),
                   vad_model_path=str(app/'models/silero_vad.onnx'))
        if cfg['hardware']=='nvidia' and os.environ.get('DUSKY_TEST_GPU_BUDGET'):
            cfg['gpu_mem_limit_mb']=int(os.environ['DUSKY_TEST_GPU_BUDGET'])
        # Preserve the install's VAD path (its filename is versioned).
        cfg['vad_model_path']=str(app/json.loads((app/'config.json').read_text())['vad_model_path'])
        config=root/'config.json';config.write_text(json.dumps(cfg))
        with wave.open(str(audio)) as wav:
            params=wav.getparams();frames=wav.readframes(wav.getnframes());duration=params.nframes/params.framerate
        long=root/'long.wav'
        with wave.open(str(long),'wb') as wav:
            wav.setparams(params)
            for _ in range(repeats):wav.writeframes(frames)
        env=dict(os.environ,XDG_RUNTIME_DIR=str(root/'runtime'),DUSKY_APP_DIR=str(root),DUSKY_CONFIG=str(config),CUDA_VISIBLE_DEVICES='-1')
        env.pop('NOTIFY_SOCKET',None);env.pop('WATCHDOG_USEC',None)
        log=open(root/'daemon.log','w+')
        proc=subprocess.Popen([sys.executable,str(Path(__file__).resolve()),'--serve',str(config)],env=env,stdout=log,stderr=log)
        endpoint=root/'runtime/dusky-stt/control.sock';pids=set();latencies=[];checks=[]
        metrics={'peak_worker_rss_mib':0,'sampled_peak_gpu_mib':0};next_gpu_sample=0
        def wait_idle():
            deadline=time.monotonic()+60
            while time.monotonic()<deadline:
                if request(endpoint,{'command':'status'})['state']=='idle':return
                time.sleep(.05)
            raise AssertionError('daemon did not return to idle')
        def job(path,cancel=False,crash=False):
            nonlocal next_gpu_sample
            wait_idle();reply=request(endpoint,{'command':'file','path':str(path)});assert reply['ok'],reply
            result=root/'state/jobs'/f"{reply['job']}.json";deadline=time.monotonic()+600;start=time.monotonic();cancelled=False;crashed=False
            while not result.exists():
                assert proc.poll() is None,'daemon exited'
                assert time.monotonic()<deadline,'job timeout'
                now=time.monotonic();status=request(endpoint,{'command':'status'});latencies.append(time.monotonic()-now)
                if status.get('worker_pid'):
                    pid=status['worker_pid'];pids.add(pid)
                    try:
                        rss=next(line.split()[1] for line in Path(f'/proc/{pid}/status').read_text().splitlines() if line.startswith('VmRSS:'))
                        metrics['peak_worker_rss_mib']=max(metrics['peak_worker_rss_mib'],int(rss)/1024)
                    except (OSError,StopIteration):pass
                    if cfg['hardware']=='nvidia' and time.monotonic()>=next_gpu_sample:
                        next_gpu_sample=time.monotonic()+2
                        result_smi=subprocess.run(['nvidia-smi','--query-compute-apps=pid,used_gpu_memory','--format=csv,noheader,nounits'],capture_output=True,text=True,timeout=5)
                        for line in result_smi.stdout.splitlines():
                            fields=line.split(',')
                            if fields[0].strip()==str(pid) and fields[1].strip().isdigit():
                                metrics['sampled_peak_gpu_mib']=max(metrics['sampled_peak_gpu_mib'],int(fields[1]))
                if cancel and not cancelled and time.monotonic()-start>.3:
                    assert request(endpoint,{'command':'stop'})['ok'];cancelled=True
                if crash and not crashed and status.get('worker_pid') and time.monotonic()-start>.25:
                    os.kill(status['worker_pid'],signal.SIGKILL);crashed=True
                time.sleep(.05)
            answer=json.loads(result.read_text());answer['elapsed_s']=time.monotonic()-start
            return answer
        try:
            deadline=time.monotonic()+15
            while not endpoint.exists():
                assert proc.poll() is None,'daemon startup failed';assert time.monotonic()<deadline;time.sleep(.05)
            with socket.socket(socket.AF_UNIX,socket.SOCK_SEQPACKET) as sock:
                sock.connect(str(endpoint));sock.send(b'[]');assert not json.loads(sock.recv(4096))['ok']
            checks.append('non-object control message rejected')
            result=job(long);assert result['ok'],result
            text=Path(result['path']).read_text();assert 'reliable transcription test' in text.lower(),text
            assert text.lower().count('interruption')==repeats,(text,repeats)
            reference=audio.with_suffix('.txt')
            if reference.exists():
                assert re.findall(r'\w+',text.casefold())==re.findall(r'\w+',reference.read_text().casefold())*repeats,text
                checks.append('all normalized words match the reference')
            checks.append(f'{duration*repeats:.2f}s audio: all {repeats} ending markers retained')
            bad=root/'bad.wav';bad.write_text('invalid audio');assert not job(bad)['ok'];checks.append('decoder failure reported')
            assert not job(long,cancel=True)['ok'];checks.append('cancellation reported')
            recovered=job(audio);assert recovered['ok'],recovered;checks.append('transcription recovered after failure and cancellation')
            restarted=job(audio,crash=True);assert restarted['ok'],restarted;checks.append('worker crash retried successfully')
            wait_idle();assert request(endpoint,{'command':'unload'})['ok'];checks.append('worker unload acknowledged')
            maps=Path(f'/proc/{proc.pid}/maps').read_text();assert 'libcudart' not in maps and 'libcuda.so' not in maps
            checks.append('CPU daemon stayed CUDA-free')
            report={'hardware':cfg['hardware'],'audio_s':duration*repeats,'long_job_s':result['elapsed_s'],
                    'words':len(text.split()),'markers':repeats,'gpu_arena_budget_mib':cfg.get('gpu_mem_limit_mb'),
                    'status_max_ms':round(max(latencies)*1000,2),'checks':checks,**metrics}
            print(json.dumps(report,indent=2),flush=True)
        finally:
            proc.terminate()
            try:proc.wait(timeout=15)
            except subprocess.TimeoutExpired:proc.kill();proc.wait()
            log.seek(0);logs=log.read();log.close()
            if sys.exc_info()[0]:print(logs,file=sys.stderr)
            for pid in pids:
                assert not Path(f'/proc/{pid}').exists(),f'worker {pid} survived shutdown'

if __name__=='__main__':
    if sys.argv[1]=='--serve':sys.exit(serve(Path(sys.argv[2])))
    stress(Path(sys.argv[1]),Path(sys.argv[2]),int(sys.argv[3]) if len(sys.argv)>3 else 20)
