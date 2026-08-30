const express = require('express');
const client = require('prom-client');
const axios = require('axios');

const app = express();
app.use(express.json());

const PORT = process.env.PORT || 8000;
const PAYMENT_URL = process.env.PAYMENT_SERVICE_URL || 'http://toxiproxy:18003/authorize';

const RETRIES_MAX = 8;
const RETRY_TIMEOUT_MS = 500;
const RETRY_BACKOFF_MS = 0;

// Prometheus metrics
const register = new client.Registry();
client.collectDefaultMetrics({ register });

const retryCounter = new client.Counter({
  name: 'retry_count_total',
  help: 'Total retry attempts across service boundaries',
  labelNames: ['service', 'target'],
  registers: [register],
});

const requestCounter = new client.Counter({
  name: 'checkout_requests_total',
  help: 'Total incoming checkout requests',
  labelNames: ['service', 'status'],
  registers: [register],
});

function sleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

async function callDownstreamWithRetry(orderData) {
  let attempt = 0;
  while (attempt < RETRIES_MAX) {
    attempt++;
    if (attempt > 1) {
      retryCounter.labels('express-order', 'payment').inc();
      if (RETRY_BACKOFF_MS > 0) {
        await sleep(RETRY_BACKOFF_MS);
      }
    }

    try {
      const resp = await axios.post(PAYMENT_URL, orderData, { timeout: RETRY_TIMEOUT_MS });
      return resp.data;
    } catch (err) {
      if (attempt >= RETRIES_MAX) {
        throw err;
      }
    }
  }
}

app.get('/health', (req, res) => {
  res.json({ status: 'healthy', framework: 'express-node' });
});

app.get('/metrics', async (req, res) => {
  res.set('Content-Type', register.contentType);
  res.end(await register.metrics());
});

app.post('/api/v1/orders/create', async (req, res) => {
  const { item_id, quantity } = req.body || {};
  try {
    const paymentResp = await callDownstreamWithRetry({
      order_id: `ord_${Date.now()}`,
      amount: 49.99
    });
    requestCounter.labels('express-order', 'success').inc();
    res.status(200).json({ status: 'SUCCESS', item_id: item_id || 'default_item', payment: paymentResp });
  } catch (err) {
    requestCounter.labels('express-order', 'error').inc();
    res.status(504).json({ status: 'ERROR', error: err.message });
  }
});

app.listen(PORT, () => {
  console.log(`Express Order Service running on port ${PORT}`);
});

