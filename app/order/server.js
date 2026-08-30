const express = require('express');
const axios = require('axios');
const client = require('prom-client');

const app = express();
app.use(express.json());

const collectDefaultMetrics = client.collectDefaultMetrics;
collectDefaultMetrics();

const httpRequestsTotal = new client.Counter({
  name: 'http_requests_total',
  help: 'Total HTTP requests',
  labelNames: ['method', 'route', 'status_code']
});

const retryAttemptsTotal = new client.Counter({
  name: 'retry_attempts_total',
  help: 'Total retry attempts',
  labelNames: ['service', 'target']
});

const RETRIES_MAX = 8;
const RETRY_TIMEOUT_MS = 500;
const RETRY_BACKOFF_MS = 0;

app.get('/health', (req, res) => {
  res.json({ status: 'ok', service: 'order-service', retries_max: RETRIES_MAX });
});

app.get('/metrics', async (req, res) => {
  res.set('Content-Type', client.register.contentType);
  res.end(await client.register.metrics());
});

app.post('/api/orders', async (req, res) => {
  httpRequestsTotal.inc({ method: 'POST', route: '/api/orders', status_code: '200' });
  const { orderId, amount, item } = req.body;
  const inventoryUrl = process.env.INVENTORY_SERVICE_URL || 'http://inventory-service:8000/inventory/reserve';

  for (let attempt = 0; attempt <= RETRIES_MAX; attempt++) {
    if (attempt > 0) {
      retryAttemptsTotal.inc({ service: 'order-service', target: 'inventory-service' });
      if (RETRY_BACKOFF_MS > 0) {
        await new Promise(resolve => setTimeout(resolve, RETRY_BACKOFF_MS));
      }
    }
    try {
      const resp = await axios.post(inventoryUrl, { orderId, amount, item }, { timeout: RETRY_TIMEOUT_MS });
      return res.json({ status: 'completed', orderId, inventory: resp.data });
    } catch (err) {
      if (attempt === RETRIES_MAX) {
        return res.status(503).json({ error: 'inventory service unavailable after retries', details: err.message });
      }
    }
  }
});

const PORT = process.env.PORT || 3000;
app.listen(PORT, () => {
  console.log(`Order service listening on port ${PORT}`);
});
