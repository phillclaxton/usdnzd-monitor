/**
 * Launches the real backend behind a proxy that mimics Home Assistant Ingress.
 *
 * The proxy mounts the app under a token-shaped prefix and sets X-Ingress-Path,
 * which is exactly what the Supervisor does. Running the end-to-end tests
 * through it means a regression in base-path handling fails the suite instead
 * of only failing on someone's real installation.
 */
import { spawn } from 'node:child_process';
import { createServer, request as httpRequest } from 'node:http';
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
    clientResponse.writeHead(502, { 'content-type': 'text/plain' });
    clientResponse.end(`proxy error: ${error.message}`);
  });
  clientRequest.pipe(upstream);
});

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
