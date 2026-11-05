"""Windows on-demand supervisor. Paths in --config are local, never credentials.

Recover tunnel/API/bot independently. Do not import decrypted app configuration
into this process or print subprocess output/authenticated request URLs.
"""
import argparse
import http.client
import json
import os
from pathlib import Path
import queue
import re
import subprocess
import ssl
import threading
import time
from urllib.parse import urlsplit

from dotenv import dotenv_values, set_key

DOMAIN = re.compile(r'https://[a-z0-9-]+\.(?:lhr\.life|localhost\.run|lhr\.rocks)(?![a-zA-Z0-9.-])')


def url_from_line(line):
    # Only the server's registration event may change the configured origin.
    try:
        event = json.loads(line)
    except (ValueError, UnicodeError):
        return None
    if not isinstance(event, dict) or event.get('type') != 'v1.tcpip_forward.register.accepted':
        return None
    match = DOMAIN.search(str(event.get('data', '')))
    return match.group() if match else None


def healthy(url, timeout=5):
    connection = None
    try:
        if url == 'http://127.0.0.1:8000':
            connection = http.client.HTTPConnection('127.0.0.1', 8000, timeout=timeout)
        elif DOMAIN.fullmatch(url):
            context = ssl.create_default_context()
            context.minimum_version = ssl.TLSVersion.TLSv1_2
            connection = http.client.HTTPSConnection(urlsplit(url).hostname, timeout=timeout, context=context)
        else:
            return False
        connection.request('GET', '/readyz')
        response = connection.getresponse()
        return response.status == 200 and json.loads(response.read(512)) == {'status': 'ok'}
    except Exception:
        return False
    finally:
        if connection:
            connection.close()


def recovery_decision(kind, failures, alive):
    # A temporary public-route error must never terminate the working API/bot.
    return not alive or failures >= (6 if kind == 'tunnel' else 3)


class Supervisor:
    def __init__(self, config):
        self.config = config
        self.work = Path(config['state_dir']).resolve()
        self.code = Path(config['code_dir']).resolve()
        self.runtime = Path(config['runtime_dir']).resolve()
        self.work.mkdir(parents=True, exist_ok=True)
        self.python = self.runtime / '.venv/Scripts/python.exe'
        self.children, self.logs = {}, {}
        self.urls = queue.Queue(maxsize=1)
        self.state = {'version': 2, 'phase': 'starting', 'controller_pid': os.getpid(),
                      'scheduled_reminders': 'paused', 'vk_enabled': False,
                      'restarts': {'api': 0, 'bot': 0, 'tunnel': 0},
                      'local_url': 'http://127.0.0.1:8000', 'tunnel_provider': 'localhost.run'}
        self.events = []
        self.launch_times = {}
        self.env = None

    def save(self):
        self.state['updated_at'] = time.time()
        temp = self.work / 'services.json.tmp'
        temp.write_text(json.dumps(self.state, indent=2), encoding='utf-8')
        os.replace(temp, self.work / 'services.json')

    def record(self, code):
        self.events.append({'at': time.time(), 'code': code})
        self.events = self.events[-100:]
        temp = self.work / 'recovery-events.json.tmp'
        temp.write_text(json.dumps(self.events), encoding='utf-8')
        os.replace(temp, self.work / 'recovery-events.json')
        self.state['last_event'] = code
        self.save()

    def stopping(self):
        return (self.work / 'stop-requested').exists()

    def pause(self, seconds):
        until = time.monotonic() + seconds
        while time.monotonic() < until and not self.stopping():
            time.sleep(.25)

    def alive(self, name):
        return name in self.children and self.children[name].poll() is None

    def stop(self, name):
        process = self.children.pop(name, None)
        if process and process.poll() is None:
            # Kill the owned venv launcher AND its interpreter, never by name.
            subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=20)
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
        stream = self.logs.pop(name, None)
        if stream:
            stream.close()

    def launch(self, name, command, env=None, pipe=False):
        self.stop(name)
        if pipe:
            output = subprocess.PIPE
        else:
            # Bounded previous log, so a failed restart does not destroy evidence.
            path = self.work / (name + '.log')
            if path.exists():
                previous = self.work / (name + '.previous.log')
                with path.open('rb') as old:
                    old.seek(max(0, path.stat().st_size - 512 * 1024))
                    previous.write_bytes(old.read(512 * 1024))
            output = path.open('w', encoding='utf-8')
            self.logs[name] = output
        process = subprocess.Popen(command, cwd=self.code, env=env, stdin=subprocess.DEVNULL,
                                   stdout=output, stderr=subprocess.STDOUT,
                                   creationflags=subprocess.CREATE_NO_WINDOW)
        self.children[name] = process
        if name in self.launch_times:
            self.state['restarts'][name] += 1
        self.launch_times[name] = time.time()
        self.state[name + '_pid'] = process.pid
        self.save()
        return process

    def postgres(self):
        binary = Path(self.config['postgres_bin'])
        result = subprocess.run([str(binary / 'pg_isready.exe'), '-h', '127.0.0.1', '-p', '55433', '-t', '2'],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
        if result.returncode:
            status = subprocess.run([str(binary / 'pg_ctl.exe'), '-D', self.config['postgres_data'], 'status'],
                                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
            if status.returncode:
                subprocess.run([str(binary / 'pg_ctl.exe'), '-D', self.config['postgres_data'], '-l', str(self.work / 'postgres.log'),
                                '-o', '-h 127.0.0.1 -p 55433', '-w', 'start'], stdin=subprocess.DEVNULL,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30, check=True)
                self.record('postgres_started')

    def tunnel(self):
        while not self.urls.empty():
            self.urls.get_nowait()
        command = [r'C:\Windows\System32\OpenSSH\ssh.exe', '-F', 'NUL', '-T', '-a', '-x',
                   '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
                   '-o', 'UserKnownHostsFile=' + self.config['ssh_known_hosts'],
                   '-o', 'IdentityAgent=none', '-o', 'ServerAliveInterval=15', '-o', 'ServerAliveCountMax=3',
                   '-o', 'ExitOnForwardFailure=yes', '-o', 'ConnectTimeout=12']
        identity = self.config.get('tunnel_identity_file')
        if identity:
            command += ['-i', identity, '-o', 'IdentitiesOnly=yes']
        else:
            command += ['-o', 'PubkeyAuthentication=no']
        command += ['-R', '80:127.0.0.1:8000', self.config.get('ssh_user', 'nokey') + '@localhost.run', '--', '--output', 'json']
        process = self.launch('tunnel', command, pipe=True)
        def reader():
            # Never retain raw SSH event data, request paths, headers or URLs with tokens.
            while True:
                line = process.stdout.readline(128 * 1024)
                if not line:
                    break
                url = url_from_line(line)
                if url and self.children.get('tunnel') is process:
                    try:
                        self.urls.put_nowait(url)
                    except queue.Full:
                        pass
        threading.Thread(target=reader, daemon=True).start()

    def configure(self, url):
        settings = {'WEBAPP_URL': url, 'APP_ENV': 'production', 'DEV_TELEGRAM_ID': '',
                    'ALLOWED_HOSTS': '127.0.0.1,localhost,' + url.split('://')[1],
                    'CORS_ALLOWED_ORIGINS': '', 'VK_ID_CLIENT_ID': '', 'VK_MINI_APP_ID': '',
                    'VK_MINI_APP_SECRET': '', 'MIN_FREE_STORAGE_MB': '128'}
        for name, value in settings.items():
            set_key(str(self.runtime / '.env'), name, value)
        self.env = os.environ.copy()
        self.env.update({k: v for k, v in dotenv_values(self.runtime / '.env').items() if v is not None})
        self.env['PYTHONUTF8'] = '1'
        self.state['public_url'] = url
        self.save()

    def api(self):
        self.launch('api', [str(self.python), '-m', 'uvicorn', 'api.main:app', '--host', '127.0.0.1', '--port', '8000',
                            '--proxy-headers', '--forwarded-allow-ips', '127.0.0.1', '--no-access-log'], self.env)

    def bot(self):
        self.launch('bot', [str(self.python), self.config['bot_script']], self.env)

    def bot_ready(self):
        try:
            from utils.bot_health import readiness_issues
            if (self.work / 'bot-health.json').stat().st_size > 16384:
                return False
            data = json.loads((self.work / 'bot-health.json').read_text(encoding='utf-8'))
            return data.get('started_at', 0) >= self.launch_times['bot'] - 5 and not readiness_issues(data)
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            return False

    def run(self):
        import msvcrt
        lock = (self.work / 'controller.lock').open('a+b')
        lock.seek(0); lock.write(b'0'); lock.flush(); lock.seek(0)
        try:
            msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            return
        failures = {'api': 0, 'bot': 0, 'tunnel': 0}
        retries = {'api': 0, 'bot': 0, 'tunnel': 0}
        try:
            self.save()
            old = dotenv_values(self.runtime / '.env').get('WEBAPP_URL')
            if old and DOMAIN.fullmatch(old):
                self.configure(old)
            while not self.stopping():
                try:
                    self.postgres()
                    now = time.monotonic()
                    if not self.alive('tunnel') and now >= retries['tunnel']:
                        self.tunnel(); retries['tunnel'] = now + 20
                        self.record('tunnel_connecting')
                    try:
                        url = self.urls.get_nowait()
                    except queue.Empty:
                        url = None
                    if url and url != self.state.get('public_url'):
                        # A changed origin needs a new host allowlist and Telegram menu.
                        self.stop('bot'); self.stop('api'); self.configure(url)
                        retries['api'] = retries['bot'] = 0
                        self.record('public_origin_changed')
                    if self.env and not self.alive('api') and now >= retries['api']:
                        self.api(); retries['api'] = now + 30
                    local = healthy('http://127.0.0.1:8000', timeout=4) if self.alive('api') else False
                    public = healthy(self.state['public_url']) if local and self.alive('tunnel') and self.state.get('public_url') else False
                    self.state.update(local_ready=local, public_ready=public)
                    for name, good in [('api', local), ('tunnel', public)]:
                        failures[name] = 0 if good else failures[name] + 1
                    if public:
                        self.state['last_public_success_at'] = time.time()
                        if not self.alive('bot') and now >= retries['bot']:
                            self.bot(); retries['bot'] = now + 60
                    if recovery_decision('api', failures['api'], self.alive('api')) and self.alive('api') and now >= retries['api']:
                        self.stop('api'); retries['api'] = now + 2; failures['api'] = 0
                        self.record('api_not_ready')
                    # Do not blame the tunnel for an unavailable local database/API.
                    if local and recovery_decision('tunnel', failures['tunnel'], self.alive('tunnel')) and self.alive('tunnel') and now >= retries['tunnel']:
                        self.stop('tunnel'); retries['tunnel'] = now + 2; failures['tunnel'] = 0
                        self.record('public_route_unavailable')
                    if self.alive('bot') and time.time() - self.launch_times['bot'] > 60:
                        failures['bot'] = 0 if self.bot_ready() else failures['bot'] + 1
                        if failures['bot'] >= 3 and now >= retries['bot']:
                            self.stop('bot'); retries['bot'] = now + 30; failures['bot'] = 0
                            self.record('bot_not_ready')
                    self.state['phase'] = 'running' if public and self.alive('bot') else 'degraded'
                    self.save()
                except Exception as error:
                    # No cascading shutdown and no raw exceptions/credentials in state.
                    self.state.update(local_ready=False, public_ready=False)
                    self.record('supervisor_' + type(error).__name__)
                    self.state['phase'] = 'degraded'; self.save()
                self.pause(10)
        finally:
            for name in ['bot', 'api', 'tunnel']:
                self.stop(name)
            self.state['phase'] = 'stopped'; self.save(); lock.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True)
    args = parser.parse_args()
    Supervisor(json.loads(Path(args.config).read_text(encoding='utf-8'))).run()


if __name__ == '__main__':
    main()
