let token = '';
const $ = id => document.getElementById(id);
async function api(path, method = 'GET', body) {
  const response = await fetch(path, {method, headers: {'Content-Type': 'application/json', ...(token ? {Authorization: 'Bearer ' + token} : {})}, ...(body === undefined ? {} : {body: typeof body === 'string' ? body : JSON.stringify(body)})});
  const text = await response.text(); let data;
  try { data = JSON.parse(text); } catch { data = text; }
  if (!response.ok) throw Error(response.status + ' ' + (data.error || text));
  return data;
}
function show(data) { $('result').textContent = typeof data === 'string' ? data : JSON.stringify(data, null, 2); }
async function run(action) { $('status').textContent = ''; try { await action(); } catch (e) { $('status').textContent = e.message; } }
function button(label, action) { const b = document.createElement('button'); b.textContent = label; b.onclick = () => run(action); $('actions').append(b); }
function field(value, label, type = 'text') { const box = document.createElement('label'); box.textContent = label; const x = document.createElement('input'); x.type = type; x.value = value; box.append(x); $('fields').append(box); return x; }
function edit(value) { $('editor').hidden = false; $('editor').value = typeof value === 'string' ? value : JSON.stringify(value, null, 2); }
function list(value) { return value.split(',').map(x => x.trim()).filter(Boolean); }
function table(rows, keys) {
  $('table').replaceChildren(); if (!rows.length) { show('No records.'); return; }
  const t = document.createElement('table'); const head = document.createElement('tr');
  for (const key of keys) { const th = document.createElement('th'); th.textContent = key; head.append(th); } t.append(head);
  for (const row of rows) { const tr = document.createElement('tr'); for (const key of keys) { const td = document.createElement('td'); const v = row[key]; td.textContent = typeof v === 'object' ? JSON.stringify(v) : String(v ?? ''); tr.append(td); } t.append(tr); }
  $('table').append(t); show('');
}
$('login-form').onsubmit = event => { event.preventDefault(); run(async () => { const data = await api('/api/auth/token', 'POST', {username: $('username').value, password: $('password').value}); token = data.access_token; $('password').value = ''; $('login').hidden = true; $('console').hidden = false; $('logout').hidden = false; open('Service Manager'); }); };
$('logout').onclick = () => { token = ''; location.reload(); };
for (const name of ['Service Manager', 'API Manager', 'Traffic Manager', 'Log Manager', 'Alarm Manager', 'User Manager', 'Observability']) {
  const b = document.createElement('button'); b.textContent = name; b.onclick = () => open(name); $('tabs').append(b);
}
function open(name) {
  $('title').textContent = name; $('actions').replaceChildren(); $('fields').replaceChildren(); $('table').replaceChildren(); $('editor').hidden = true; show(''); $('status').textContent = ''; $('help').textContent = '';
  for (const b of $('tabs').children) b.classList.toggle('active', b.textContent === name);
  if (name === 'Service Manager') {
    const service = field('app1', 'Service name');
    button('List services', async () => table((await api('/api/services')).map(service => ({service})), ['service']));
    button('Instances and health', async () => table(await api('/api/services/' + encodeURIComponent(service.value) + '/instances'), ['ip', 'port', 'clusterName', 'healthy', 'metadata']));
    button('Read configuration', async () => edit(await api('/api/services/' + encodeURIComponent(service.value) + '/config')));
    button('Save configuration', async () => show(await api('/api/services/' + encodeURIComponent(service.value) + '/config', 'PUT', $('editor').value)));
    $('help').textContent = 'Registration and live configuration from GRDC. Try message=Hello. Configuration changes require ADMIN.';
  }
  if (name === 'API Manager') {
    const service = field('app1', 'Service (app1 / app2)'), roles = field('API,ADMIN', 'Allowed roles'), methods = field('GET,POST', 'HTTP methods'), rate = field('20', 'Requests per second', 'number'), allow = field('', 'Allowed users (optional)'), deny = field('', 'Denied users (optional)');
    const enabled = field('true', 'Enabled (true / false)');
    button('List policies', async () => table(await api('/api/apis'), ['service', 'enabled', 'roles', 'methods', 'requestsPerSecond']));
    button('Save draft', async () => show(await api('/api/apis', 'PUT', {service: service.value, enabled: enabled.value === 'true', roles: list(roles.value), allowedUsers: list(allow.value), deniedUsers: list(deny.value), methods: list(methods.value), requestsPerSecond: Number(rate.value)})));
    button('Publish all policies', async () => show(await api('/api/apis/publish', 'POST')));
    $('help').textContent = 'Save a policy for each service, then publish. Unpublished services are denied. The rate counter is shared across both sites.';
  }
  if (name === 'Traffic Manager') {
    edit({query: 'query { getGlobalBrief(isBrandNew: false) { numOfService numOfEndpoint numOfDatabase numOfCache numOfMQ } }', variables: {}});
    button('Query SkyWalking', async () => show(await api('/api/traffic/graphql', 'POST', JSON.parse($('editor').value))));
    $('help').textContent = 'SkyWalking provides the dependency graph and distributed traces. Query it here or open its full UI using the port-forward command in docs/deployment.md.';
  }
  if (name === 'Log Manager') {
    const query = field('', 'Search application messages');
    button('Search last 24 hours', async () => { const result = await api('/api/logs?text=' + encodeURIComponent(query.value)); table((result.hits?.hits || []).map(x => ({time: x._source['@timestamp'], service: x._source.platform?.service, site: x._source.platform?.site, message: x._source.message})), ['time', 'service', 'site', 'message']); });
  }
  if (name === 'Alarm Manager') {
    edit([{alert: 'ApplicationDown', expr: 'up{job="platform-apps"} == 0', forSeconds: 60, severity: 'critical'}]);
    button('Active alerts', async () => table((await api('/api/alarms')).map(x => ({alert: x.labels.alertname, severity: x.labels.severity, state: x.status.state, startsAt: x.startsAt})), ['alert', 'severity', 'state', 'startsAt']));
    button('Read rules', async () => edit(await api('/api/alarms/rules')));
    button('Publish rules', async () => show(await api('/api/alarms/rules', 'PUT', JSON.parse($('editor').value))));
    $('help').textContent = 'Config-sync validates PromQL and reloads Prometheus. Use Prometheus Rules to confirm activation. Notification receivers are configured separately.';
  }
  if (name === 'User Manager') {
    const username = field('reader', 'Username'), password = field('', 'New password (12–72 characters)', 'password'), roles = field('VIEWER', 'Roles (ADMIN, VIEWER, API)'), enabled = field('true', 'Enabled (true / false)');
    button('List users', async () => table(await api('/api/users'), ['username', 'roles', 'enabled']));
    button('Save user', async () => { show(await api('/api/users', 'PUT', {username: username.value, password: password.value, roles: list(roles.value), enabled: enabled.value === 'true'})); password.value = ''; });
    button('Audit trail', async () => table(await api('/api/audit'), ['occurredAt', 'actor', 'action', 'target']));
    $('help').textContent = 'Disabling users blocks new tokens. Existing tokens expire within 15 minutes.';
  }
  if (name === 'Observability') {
    const query = field('up', 'PromQL');
    button('Run query', async () => { const data = await api('/api/observability/query?query=' + encodeURIComponent(query.value)); table((data.data?.result || []).map(x => ({metric: x.metric, value: x.value?.[1], timestamp: x.value?.[0]})), ['metric', 'value', 'timestamp']); });
    $('help').textContent = 'Query Prometheus directly. Grafana includes the provisioned platform dashboard; open it using docs/deployment.md.';
  }
}
