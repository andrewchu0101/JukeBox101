"""Verify the local TLS setup and all PG tests in fresh, uniquely named Docker resources.

Requires the prebuilt test image. Optional DOCKER and TEST_IMAGE overrides.
Passwords remain in process memory; removes only resources created by this invocation.
"""
import os,secrets,subprocess,time
DK=os.environ.get('DOCKER', 'docker')
suffix=secrets.token_hex(5);name='jb-v7-tls-repro-'+suffix;volume=name+'-data';network=name+'-net';password=secrets.token_urlsafe(32)
def run(args,check=True):
 p=subprocess.run([DK,*args],text=True,capture_output=True)
 if check and p.returncode:raise RuntimeError('Docker operation failed: '+args[0]+': '+p.stderr)
 return p
created_volume=created_network=created_container=False
try:
 run(['network','create',network]);created_network=True
 run(['volume','create',volume]);created_volume=True
 run(['run','-d','--name',name,'--network',network,'--network-alias','postgres','--mount',f'type=volume,source={volume},target=/var/lib/postgresql','-e','POSTGRES_PASSWORD='+password,'postgres:18.6-bookworm','-c','cluster_name=jukebox101-disposable-test']);created_container=True
 for attempt in range(60):
  if run(['exec','-u','postgres',name,'pg_isready','-U','postgres'],False).returncode==0:break
  time.sleep(1)
 else:raise RuntimeError('disposable server not ready')
 env=os.environ.copy();env.update(DOCKER=DK,PG_CONTAINER=name)
 p=subprocess.run(['bash','scripts/dev/enable-test-tls.sh'],env=env,text=True,capture_output=True)
 if p.returncode:raise RuntimeError(p.stderr)
 print(p.stdout,flush=True)
 result=run(['run','--rm','--network',network,'-e','JB_PG_TEST_HOST=postgres','-e','JB_PG_TEST_ADMIN_USER=postgres','-e','JB_PG_TEST_ADMIN_PASSWORD='+password,os.environ.get('TEST_IMAGE', 'jukebox101-test:v7-local'),'-q','-p','no:cacheprovider','-m','postgres','-rfEs'])
 print(result.stdout,flush=True)
 if '35 passed' not in result.stdout or 'skipped' in result.stdout:raise RuntimeError('require 35 tests with zero skips')
 print('FRESH TLS SETUP VERIFIED',flush=True)
finally:
 if created_container:run(['rm','-f',name])
 if created_volume:run(['volume','rm',volume])
 if created_network:run(['network','rm',network])
 print('Dedicated fresh TLS resources removed',flush=True)
