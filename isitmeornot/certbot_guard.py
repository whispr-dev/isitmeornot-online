#!/usr/bin/env python3
"""Retry Certbot's own lock-contention error without touching locks or other jobs."""
from __future__ import annotations
import argparse
import os
from pathlib import Path
import subprocess
import sys
import time

LOCK_MESSAGE='another instance of certbot is already running'


class CertbotBusy(RuntimeError):
    pass


def run_certbot(arguments,wait_seconds=600,retry_seconds=10,*,clock=None,sleeper=None,runner=None):
    if wait_seconds<0 or retry_seconds<=0:
        raise ValueError('Invalid Certbot retry interval')
    clock=clock or time.monotonic
    sleeper=sleeper or time.sleep
    runner=runner or subprocess.run
    deadline=clock()+wait_seconds
    environment=dict(os.environ,LC_ALL='C',LANG='C')
    attempt=0
    while True:
        result=runner(list(arguments),check=False,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,env=environment)
        output=result.stdout or ''
        busy=result.returncode!=0 and LOCK_MESSAGE in output.lower()
        if output and (not busy or attempt==0):
            print(output,end='' if output.endswith('\n') else '\n',flush=True)
        if not busy:
            return result  # Genuine issuance failures are never retried automatically.
        remaining=deadline-clock()
        if remaining<=0:
            raise CertbotBusy(f'Certbot stayed locked during the {wait_seconds:g}-second wait window. '
                              'Another certificate job still holds its lock. No other job was stopped and no '
                              'lock file was changed. Let that job finish, then rerun deployment.')
        delay=min(retry_seconds,remaining)
        print(f'Certbot is busy; waiting {delay:g}s before retrying ({remaining:g}s left in lock wait window).',flush=True)
        sleeper(delay)
        attempt+=1


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--wait-seconds',type=int,default=600)
    parser.add_argument('command',nargs=argparse.REMAINDER)
    args=parser.parse_args()
    command=args.command[1:] if args.command[:1]==['--'] else args.command
    if not command or Path(command[0]).name!='certbot':
        parser.error('Pass -- followed by the Certbot executable and its arguments.')
    try:
        return run_certbot(command,wait_seconds=args.wait_seconds).returncode
    except (CertbotBusy,ValueError,OSError) as error:
        print('ERROR: '+str(error),file=sys.stderr)
        return 1

if __name__=='__main__':sys.exit(main())
