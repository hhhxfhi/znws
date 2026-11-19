import {spawn, execFile} from 'node:child_process';
import {promisify} from 'node:util';
import {mkdir, readFile, rm, access} from 'node:fs/promises';
import {randomBytes, randomUUID} from 'node:crypto';
import {fileURLToPath} from 'node:url';
import {join, resolve} from 'node:path';
import {setTimeout as delay} from 'node:timers/promises';
import {diagnostics} from './diagnostics.js';

const root = fileURLToPath(new URL('./', import.meta.url));
const execute = promisify(execFile);
function childEnvironment(extra = {}) {
  const value = {PYTHONUTF8: '1', PYTHONIOENCODING: 'utf-8'};
  for (const key of ['PATH', 'LANG', 'LC_ALL', 'SYSTEMROOT', 'USERNAME', 'USERPROFILE', 'TMPDIR', 'TEMP', 'TMP']) if (process.env[key]) value[key] = process.env[key];
  return {...value, ...extra};
}
async function stopChild(child) {
  if (!child.pid) return;
  if (child.exitCode !== null || child.signalCode !== null) return;
  const kill = async signal => {
    try {if (process.platform === 'win32') child.kill(signal); else process.kill(-child.pid, signal);} catch (error) {
      if (error.code === 'ESRCH') return;
      // A just-killed process may disappear before Node dispatches its exit
      // event. macOS can report EPERM for that stale process group. Only
      // accept the race once this exact owned ChildProcess confirms exit;
      // permission failures for a live process must remain visible.
      if (error.code === 'EPERM') {
        for (let n=0;n<4&&child.exitCode===null&&child.signalCode===null;n++) await delay(25);
        if (child.exitCode!==null||child.signalCode!==null) return;
      }
      throw error;
    }
  };
  await kill('SIGTERM');
  for (let n = 0; n < 50 && child.exitCode === null && child.signalCode === null; n++) await delay(100);
  if (child.exitCode === null && child.signalCode === null) {
    await kill('SIGKILL');
    if (child.exitCode !== null || child.signalCode !== null) return;
    await new Promise(done => child.once('exit', done));
  }
}
async function prepare(dataRoot, signal) {
  signal.throwIfAborted();
  let bootstrap;
  for (const candidate of ['python3.11', 'python3.12', 'python3']) {
    try {
      await execute(candidate, ['-c', 'import sys; sys.exit(0 if sys.version_info[:2] in {(3,11),(3,12)} else 1)'], {env:childEnvironment(), timeout:2000, signal});
      bootstrap=candidate; break;
    } catch {signal.throwIfAborted();}
  }
  if (!bootstrap) {
    try {bootstrap=(await execute('uv',['python','find','--offline','>=3.11,<3.13'],{env:childEnvironment({UV_CACHE_DIR:join(dataRoot,'runtime/uv-cache')}),timeout:3000,signal})).stdout.trim();} catch {signal.throwIfAborted();}
  }
  if (!bootstrap) throw Error('问数需要 Python 3.11 或 3.12，也可通过插件 config.python 指定已准备的环境。');
  const log=await diagnostics(dataRoot,'setup');
  const child = spawn(bootstrap, [join(root, 'setup.py'), '--home', dataRoot, '--quiet'], {env: childEnvironment(), stdio: ['ignore','pipe','pipe'], detached: process.platform !== 'win32'});
  log.attach(child);
  const abort = () => {void stopChild(child);};
  signal.addEventListener('abort', abort, {once: true});
  try {
    await new Promise((done, fail) => {
      child.once('error', () => fail(Error('无法运行 Python；诊断日志：'+log.path)));
      child.once('close', code => code === 0 ? done() : fail(Error('问数 Python 环境准备失败；诊断日志：'+log.path)));
    });
    signal.throwIfAborted();
    return join(dataRoot, 'runtime', 'venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
  } finally {signal.removeEventListener('abort', abort);}
}
export async function startEngine({dataRoot, python, signal = new AbortController().signal}) {
  dataRoot = resolve(dataRoot);
  signal.throwIfAborted();
  await mkdir(dataRoot, {recursive: true, mode: 0o700});
  if (!python) {
    const existing = join(dataRoot, 'runtime', 'venv', process.platform === 'win32' ? 'Scripts/python.exe' : 'bin/python');
    // Upgrade an already working installation without pip, network or config changes.
    // A missing interpreter is the fresh-install path; a broken existing one is not.
    try {await access(existing); python = existing;}
    catch (error) {if (error.code !== 'ENOENT') throw error;}
    python ||= await prepare(dataRoot, signal);
  }
  signal.throwIfAborted();
  const checkLog=await diagnostics(dataRoot,'runtime-check');
  try {
    await execute(python,[join(root,'check_runtime.py'),'--compatible'],{env:childEnvironment(),timeout:90000,signal});
  } catch(error) {
    checkLog.write(error.stderr||error.message);
    signal.throwIfAborted();
    throw Error('现有 Python 环境缺少必要运行能力；未下载或修改依赖，原配置和历史已保留；诊断日志：'+checkLog.path);
  } finally {checkLog.close();}
  const token = randomBytes(40).toString('base64url');
  const readyFile = join(dataRoot, 'runtime', 'engine-' + randomUUID() + '.json');
  await mkdir(join(dataRoot, 'runtime'), {recursive: true, mode: 0o700});
  const log=await diagnostics(dataRoot,'engine',[token]);
  const child = spawn(python, [join(root, 'engine', 'run_plugin.py'), readyFile], {
    cwd: root, stdio: ['ignore','pipe','pipe'], detached: process.platform !== 'win32',
    env: childEnvironment({PYTHONDONTWRITEBYTECODE: '1', PYTHONPATH: join(root, 'engine'), NATURAL_DATA_HOME: dataRoot, NATURAL_DATA_TOKEN: token, NATURAL_WORK_PLUGIN: '1', ...(process.env.NATURAL_WORK_PRINCIPAL ? {NATURAL_WORK_PRINCIPAL: process.env.NATURAL_WORK_PRINCIPAL} : {})})
  });
  log.attach(child);
  let spawnError;
  child.once('error', error => {spawnError = error;});
  let stopTask;
  const stop = () => stopTask ||= (async () => {await stopChild(child); await rm(readyFile, {force: true});})();
  const abort = () => {void stop();};
  signal.addEventListener('abort', abort, {once: true});
  try {
    for (let n = 0; n < 150; n++) {
      signal.throwIfAborted();
      if (spawnError || child.exitCode !== null || child.signalCode !== null) throw Error('问数数据引擎启动失败；诊断日志：'+log.path);
      let state;
      try {state = JSON.parse(await readFile(readyFile, 'utf8'));} catch (error) {if (error.code !== 'ENOENT' && !(error instanceof SyntaxError)) throw error;}
      if (state?.port) {
        const endpoint = 'http://127.0.0.1:' + state.port;
        try {
          const response = await fetch(endpoint + '/health', {headers: {authorization: 'Bearer ' + token}, signal: AbortSignal.any([signal, AbortSignal.timeout(500)])});
          if (response.ok) return {
            endpoint, token, pid: child.pid, dataRoot,
            isAlive: () => Boolean(child.pid && !spawnError && !stopTask && child.exitCode === null && child.signalCode === null),
            stop: async () => {signal.removeEventListener('abort', abort); await stop();},
          };
        } catch (error) {signal.throwIfAborted();}
      }
      await delay(100);
    }
    throw Error('问数数据引擎未在启动期限内就绪；诊断日志：'+log.path);
  } catch (error) {signal.removeEventListener('abort', abort); await stop(); throw error;}
}
