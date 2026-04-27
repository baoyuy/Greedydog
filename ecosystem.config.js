const fs = require('fs');
const path = require('path');

function readEnvFile(filePath) {
  const env = {};
  if (!fs.existsSync(filePath)) {
    return env;
  }

  const content = fs.readFileSync(filePath, 'utf8');
  for (const line of content.split(/\r?\n/)) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith('#')) continue;
    const eqIndex = trimmed.indexOf('=');
    if (eqIndex === -1) continue;
    const key = trimmed.slice(0, eqIndex).trim();
    const value = trimmed.slice(eqIndex + 1).trim();
    env[key] = value;
  }
  return env;
}

const envPath = path.join(__dirname, '.env');
const fileEnv = readEnvFile(envPath);
const dashboardHost = process.env.DASHBOARD_HOST || fileEnv.DASHBOARD_HOST || '0.0.0.0';
const dashboardPort = process.env.DASHBOARD_PORT || fileEnv.DASHBOARD_PORT || '8080';
const autoStartStrategy = (process.env.DASHBOARD_AUTO_START_STRATEGY || fileEnv.DASHBOARD_AUTO_START_STRATEGY || 'false').toLowerCase() === 'true';

module.exports = {
  apps: [
    {
      name: 'quant-dashboard',
      script: './.venv/bin/python',
      args: `dashboard.py --host ${dashboardHost} --port ${dashboardPort}${autoStartStrategy ? ' --auto-start-strategy' : ''}`,
      cwd: './',
      interpreter: 'none',
      autorestart: true,
      watch: false,
      max_memory_restart: '500M',
      env: {
        PYTHONUNBUFFERED: '1',
        DASHBOARD_HOST: dashboardHost,
        DASHBOARD_PORT: String(dashboardPort),
        DASHBOARD_AUTO_START_STRATEGY: autoStartStrategy ? 'true' : 'false'
      },
      error_file: './runtime/logs/pm2-err.log',
      out_file: './runtime/logs/pm2-out.log',
      log_date_format: 'YYYY-MM-DD HH:mm:ss',
      merge_logs: true
    }
  ]
};
