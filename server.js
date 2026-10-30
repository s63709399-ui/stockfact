const express = require('express');
const http = require('http');
const path = require('path');
const { createProxyMiddleware } = require('http-proxy-middleware');

const app = express();
const API = process.env.API_URL || 'http://api:8000';

// Toutes les requêtes /api/* sont transmises à l'API Python.
// keepAlive désactivé : évite les connexions périmées après un redémarrage du conteneur api.
app.use(createProxyMiddleware({
  target: API,
  changeOrigin: true,
  pathFilter: '/api',
  agent: new http.Agent({ keepAlive: false }),
  proxyTimeout: 180000,
  timeout: 180000,
  on: {
    error: (err, req, res) => {
      if (res && res.writeHead && !res.headersSent) res.writeHead(502, { 'Content-Type': 'application/json' });
      if (res && res.end) res.end(JSON.stringify({ detail: 'API injoignable (' + (err.code || err.message) + ') - essayez : docker compose restart api web' }));
    },
  },
}));
app.use(express.static(path.join(__dirname, 'public')));

app.listen(3000, () => console.log('StockFact sur http://localhost:3000 -> API ' + API));
