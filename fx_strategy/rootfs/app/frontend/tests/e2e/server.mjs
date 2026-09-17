/**
 * Launches the real backend behind a proxy that mimics Home Assistant Ingress.
 *
 * The proxy mounts the app under a token-shaped prefix and sets X-Ingress-Path,
 * which is exactly what the Supervisor does. Running the end-to-end tests
 * through it means a regression in base-path handling fails the suite instead
 * of only failing on someone's real installation.
 */
import { spawn } from 'node:child_process';
import { Agent, createServer, request as httpRequest } from 'node:http';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const frontendRoot = resolve(here, '../..');
const backendRoot = resolve(frontendRoot, '../backend');

const PORT = Number(process.env.FX_E2E_PORT ?? 8199);
const BACKEND_PORT = PORT + 1;
export const INGRESS_PREFIX = '/api/hassio_ingress/E2ETESTTOKEN';

const dataDir = mkdtempSync(join(tmpdir(), 'fx-e2e-'));
const python = join(backendRoot, '.venv/bin/python');
const alembic = join(backendRoot, '.venv/bin/alembic');

const env = {
  ...process.env,
  FX_DATA_DIR: dataDir,
  FX_STATIC_DIR: join(frontendRoot, 'dist'),
  FX_LOG_LEVEL: 'warning',
  FX_TESTING: 'true',
  PYTHONPATH: backendRoot,
};

function run(command, args) {
  return new Promise((resolvePromise, rejectPromise) => {
    const child = spawn(command, args, { cwd: backendRoot, env, stdio: 'inherit' });
    child.on('exit', (code) =>
      code === 0 ? resolvePromise() : rejectPromise(new Error(`${command} exited ${code}`)),
    );
  });
}

await run(alembic, ['upgrade', 'head']);

const backend = spawn(
  python,
  [
    '-m',
    'uvicorn',
    'app.main:app',
    '--host',
    '127.0.0.1',
    '--port',
    String(BACKEND_PORT),
    '--log-level',
    'warning',
  ],
  { cwd: backendRoot, env, stdio: 'inherit' },
);

/**
 * A fresh connection per request, deliberately.
 *
 * Node's global agent has kept sockets alive since Node 19, and uvicorn closes
 * an idle one after five seconds of its own, so a request arriving as the two
 * timers meet can be written to a socket the backend is already closing. Not
 * reusing sockets removes that race, and the extra handshake against a local
 * port costs nothing at this scale.
 */
const upstreamAgent = new Agent({ keepAlive: false });

/** Strip the ingress prefix and forward, exactly as the Supervisor does. */
const proxy = createServer((clientRequest, clientResponse) => {
  const url = clientRequest.url ?? '/';
  const path = url.startsWith(INGRESS_PREFIX) ? url.slice(INGRESS_PREFIX.length) || '/' : url;

  const upstream = httpRequest(
    {
      host: '127.0.0.1',
      port: BACKEND_PORT,
      method: clientRequest.method,
      path,
      agent: upstreamAgent,
      headers: {
        ...clientRequest.headers,
        'x-ingress-path': INGRESS_PREFIX,
        'x-remote-user-display-name': 'End to end',
      },
    },
    (upstreamResponse) => {
      clientResponse.writeHead(upstreamResponse.statusCode ?? 502, upstreamResponse.headers);
      upstreamResponse.pipe(clientResponse);
    },
  );
  upstream.on('error', (error) => {
    // Said out loud, because a 502 reaches the test as a bare status code and
    // the assertion it fails is usually nothing to do with the cause.
    process.stderr.write(
      `proxy error: ${error.code ?? error.message} on ${clientRequest.method} ${path}\n`,
    );
    clientResponse.writeHead(502, { 'content-type': 'text/plain' });
    clientResponse.end(`proxy error: ${error.message}`);
  });
  clientRequest.pipe(upstream);
});

/**
 * Wait for the backend before opening the port.
 *
 * Playwright treats the port being open as "the server is ready" and navigates
 * the moment it is. The proxy can listen immediately; uvicorn cannot, because
 * its lifespan opens the database and materialises the settings first. Opening
 * the port first therefore lets the first navigation arrive before there is
 * anything to forward it to, and it fails whichever assertion happens to be
 * first rather than saying the backend was not up. Waiting here makes the open
 * port mean what Playwright reads it as.
 */
async function backendIsUp() {
  return new Promise((settle) => {
    const probe = httpRequest(
      { host: '127.0.0.1', port: BACKEND_PORT, path: '/api/v1/health', agent: upstreamAgent },
      (response) => {
        response.resume();
        settle(true);
      },
    );
    probe.on('error', () => settle(false));
    probe.end();
  });
}

const deadline = Date.now() + 60_000;
while (!(await backendIsUp())) {
  if (Date.now() > deadline) {
    throw new Error('the backend did not answer /api/v1/health within 60 seconds');
  }
  await new Promise((wait) => setTimeout(wait, 100));
}

proxy.listen(PORT, '127.0.0.1', () => {
  process.stdout.write(`e2e proxy listening on ${PORT}, ingress prefix ${INGRESS_PREFIX}\n`);
});

const shutdown = () => {
  backend.kill('SIGTERM');
  proxy.close();
  try {
    rmSync(dataDir, { recursive: true, force: true });
  } catch {
    // The temporary directory is best-effort cleanup.
  }
  process.exit(0);
};

process.on('SIGTERM', shutdown);
process.on('SIGINT', shutdown);
