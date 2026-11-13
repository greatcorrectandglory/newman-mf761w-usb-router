"""Build the ARM runtime from a private working copy of the pinned upstream sources."""
from pathlib import Path
import argparse, os, subprocess, shutil, tempfile
from concurrent.futures import ThreadPoolExecutor
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--source', type=Path, required=True)
parser.add_argument('--openssl', type=Path, required=True)
parser.add_argument('--zig', type=Path, required=True)
parser.add_argument('--output', type=Path, required=True)
args = parser.parse_args()
ssl, zig, binary = args.openssl.resolve(), args.zig.resolve(), args.output.resolve()
binary.parent.mkdir(parents=True, exist_ok=True)
if not all((ssl / name).is_file() for name in ('libcrypto.a','libssl.a')) or not zig.is_file():
    raise SystemExit('Provide the ARM OpenSSL static libraries and Zig compiler')
if not (args.source / 'src/main.c').is_file():raise SystemExit('Missing upstream v2.0.0 source')
scratch = tempfile.TemporaryDirectory(dir=binary.parent)
src = Path(scratch.name) / 'upstream'
for folder in ('src', 'include'):
    shutil.copytree(args.source / folder, src / folder)

main=src/'src/main.c'
s=main.read_text(encoding='utf8')
import re
s, matches=re.subn(r'^#define MAX_CLIENT_CONNECTIONS (?:5|10|12|20|256)$','#define MAX_CLIENT_CONNECTIONS 20',s,flags=re.MULTILINE)
if matches!=1:raise ValueError('Unknown connection worker limit')
old='if (pthread_create(&th, NULL, client_worker, ctx) != 0) {'
new='''pthread_attr_t attributes;
        int err = pthread_attr_init(&attributes);
        if (!err) {
            err = pthread_attr_setstacksize(&attributes, 262144);
            if (!err) err = pthread_create(&th, &attributes, client_worker, ctx);
            pthread_attr_destroy(&attributes);
        }
        if (err) {'''
if old in s:s=s.replace(old,new)
elif new not in s:raise ValueError('Unknown worker version')
main.write_bytes(s.encode())
resolver=src/'src/resolver.c'
r=resolver.read_text(encoding='utf-8')
old_dns='if (pthread_create(&thread, NULL, resolver_worker, NULL) != 0) break;'
if old_dns in r:
    r=r.replace('#define RESOLVER_WORKERS 4','#define RESOLVER_WORKERS 1')
    r=r.replace(old_dns,'''pthread_attr_t attr;
        if (pthread_attr_init(&attr)) break;
        int error=pthread_attr_setstacksize(&attr, 131072);
        if (!error) error=pthread_create(&thread, &attr, resolver_worker, NULL);
        pthread_attr_destroy(&attr);
        if (error) break;''')
    resolver.write_bytes(r.encode())
handshake=src/'src/tls_handshake.c'
tls=handshake.read_text(encoding='utf-8')
if '#ifdef OPENSSL_NO_DH' not in tls:
    marker='        size_t offset=0,p_size=0,g_size=0,y_size=0;'
    if tls.count(marker)!=1:raise ValueError('Unknown TLS 1.2 implementation')
    tls=tls.replace(marker,'#ifdef OPENSSL_NO_DH\n        goto cleanup;\n#else\n'+marker)
    tls=tls.replace('        client->len+=public_length;','        client->len+=public_length;\n#endif')
    tls=tls.replace('DH_free(parameters); DH_free(generated);','\n#ifndef OPENSSL_NO_DH\n    DH_free(parameters); DH_free(generated);\n#endif')
    handshake.write_bytes(tls.encode())
env = os.environ.copy()
obj=src.parent/'arm-obj';obj.mkdir(exist_ok=True)
opts=[str(zig),'cc','-target','arm-linux-musleabi','-mcpu=arm1176jz_s','-mfloat-abi=soft','-Oz','-fno-sanitize=all','-ffunction-sections','-fdata-sections','-I'+str(src/'include'),'-I'+str(ssl/'include')]
def compile(f):
    output=obj/(f.stem+'.o')
    subprocess.run(opts+['-c',str(f),'-o',str(output)],env=env,check=True)
    return str(output)
with ThreadPoolExecutor(5) as pool:objs=list(pool.map(compile, sorted((src/'src').glob('*.c'))))
subprocess.run(opts+['-static','-Wl,--gc-sections','-Wl,--strip-all']+objs+[str(ssl/'libssl.a'),str(ssl/'libcrypto.a'),'-lpthread','-o',str(binary)],env=env,check=True)
scratch.cleanup()
print('ARM core bytes',binary.stat().st_size)
