"""Exercise real PipeWire/PortAudio/VAD using a disposable virtual microphone.
Run with daemon Python: stress_microphone.py APP SPEECH.wav.
No physical microphone or default-device changes are used.
"""
import json,os,re,subprocess,sys,tempfile,time,wave
from pathlib import Path
from stress_parakeet import ROOT,request

def stress(app,audio):
    sink=f'dusky_stt_audit_{os.getpid()}'
    module=subprocess.run(['pactl','load-module','module-null-sink',f'sink_name={sink}'],capture_output=True,text=True,check=True).stdout.strip()
    try:
        with tempfile.TemporaryDirectory(prefix='parakeet-microphone-') as td:
            root=Path(td);config=root/'config.json';cfg=json.loads((app/'config.json').read_text())
            cfg.update(state_dir=str(root/'state'),notifications=False,output_mode='clipboard',input_device='pulse',
                       worker_python=str(app/'.venv-worker/bin/python'),worker_script=str(ROOT/'dusky_worker.py'),
                       vad_model_path=str(app/cfg['vad_model_path']))
            config.write_text(json.dumps(cfg))
            real_runtime=os.environ['XDG_RUNTIME_DIR']
            env=dict(os.environ,XDG_RUNTIME_DIR=str(root/'runtime'),PULSE_SERVER=f'unix:{real_runtime}/pulse/native',PULSE_SOURCE=f'{sink}.monitor',CUDA_VISIBLE_DEVICES='-1')
            env.pop('NOTIFY_SOCKET',None);env.pop('WATCHDOG_USEC',None)
            log=open(root/'daemon.log','w+')
            proc=subprocess.Popen([sys.executable,str(ROOT/'tests/stress_parakeet.py'),'--serve',str(config)],env=env,stdout=log,stderr=log)
            endpoint=root/'runtime/dusky-stt/control.sock'
            try:
                deadline=time.monotonic()+15
                while not endpoint.exists():
                    assert proc.poll() is None;assert time.monotonic()<deadline;time.sleep(.05)
                assert request(endpoint,{'command':'start','mode':'push'})['ok']
                time.sleep(.5)
                assert request(endpoint,{'command':'pause'})['event']=='paused'
                assert request(endpoint,{'command':'pause'})['event']=='resumed'
                # Two utterances with a natural pause exercise VAD finalization.
                for _ in range(2):
                    subprocess.run(['pw-play','--target',sink,str(audio)],check=True,timeout=30)
                    time.sleep(1)
                assert request(endpoint,{'command':'stop'})['ok']
                deadline=time.monotonic()+60
                while request(endpoint,{'command':'status'})['state']!='idle':
                    assert time.monotonic()<deadline;time.sleep(.1)
                transcripts=list((root/'state/transcripts').glob('*.txt'));assert len(transcripts)==1,transcripts
                text=transcripts[0].read_text()
                reference=audio.with_suffix('.txt').read_text()
                assert re.findall(r'\w+',text.casefold())==re.findall(r'\w+',reference.casefold())*2,text
                print(json.dumps({'hardware':cfg['hardware'],'checks':['real PortAudio capture from PipeWire virtual source','pause and resume','Silero VAD and asynchronous finalization','two complete utterances match reference','clean stop'],'words':len(text.split())},indent=2))
            finally:
                proc.terminate()
                try:proc.wait(timeout=15)
                except subprocess.TimeoutExpired:proc.kill();proc.wait()
                log.seek(0);logs=log.read();log.close()
                if sys.exc_info()[0]:print(logs,file=sys.stderr)
    finally:
        subprocess.run(['pactl','unload-module',module],check=True)

if __name__=='__main__':stress(Path(sys.argv[1]),Path(sys.argv[2]))
