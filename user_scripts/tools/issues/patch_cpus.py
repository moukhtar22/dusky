#!/usr/bin/env python3
import os
import sys

def patch(path):
    with open(path, 'rb') as f:
        data = f.read()

    marker = b'function lazyCpus({ cpus, hostCpuCount }) {'
    start = data.find(marker)
    if start < 0:
        print(f"{path}: lazyCpus not found, skipping")
        return False

    tail_marker = b'\n    return array;\n  };\n}\nfunction bound(binding)'
    end = data.find(tail_marker, start)
    if end < 0:
        print(f"{path}: end marker not found, skipping")
        return False
    end += len(b'\n    return array;\n  };\n}')

    orig_len = end - start
    orig = data[start:end]

    repl = b'''function lazyCpus({ cpus, hostCpuCount }) {
  return () => {
    let array;
    try {
      array = cpus();
    } catch (e) {
      array = new @Array(hostCpuCount);
      for (let i = 0; i < array.length; i++)
        array[i] = {
          model: "unknown",
          speed: 0,
          times: { user: 0, nice: 0, sys: 0, idle: 0, irq: 0 },
        };
    }
    return array;
  };
}'''

    if len(repl) > orig_len:
        print(f"{path}: replacement too long ({len(repl)} > {orig_len})")
        return False

    pad = orig_len - len(repl)
    repl = repl + b' ' * pad

    if len(repl) != orig_len:
        print(f"{path}: padding mismatch {len(repl)} != {orig_len}")
        return False

    new = data[:start] + repl + data[end:]
    tmp = path + '.cpusfix.tmp'
    with open(tmp, 'wb') as f:
        f.write(new)
    os.chmod(tmp, os.stat(path).st_mode & 0o7777)
    os.replace(tmp, path)

    print(f"{path}: patched ok (func {orig_len} bytes -> {len(repl)} bytes)")
    return True

if __name__ == '__main__':
    ok = all(patch(p) for p in sys.argv[1:])
    sys.exit(0 if ok else 1)
