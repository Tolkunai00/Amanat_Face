# Frontend

Standalone Vite web application. It has no database, camera-driver or Python
imports and communicates only with the backend `/api/v1` contract.

## Local run

```bash
cp .env.example .env
npm install
npm run dev
```

During local development, keep `VITE_API_BASE_URL` empty. Vite proxies `/api`
to `http://127.0.0.1:8080`, which keeps session cookies on the frontend origin.

## Production build

```bash
VITE_API_BASE_URL=https://api.example.com npm run build
```

The generated `dist/` directory can be deployed to any static host. A container
build is also available:

```bash
docker build --build-arg VITE_API_BASE_URL=https://api.example.com -t amanat-frontend frontend
docker run -p 80:80 amanat-frontend
```

The backend must include the frontend origin in `ALLOWED_ORIGINS`.
