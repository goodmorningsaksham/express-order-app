# Express Order App

A distributed microservice application using Express / Node.js protected by [ChangeProof](https://github.com/goodmorningsaksham/ChangeProof).

## Architecture
- **Order Service** (Express / Node.js on port 8000)
- **Payment Service** (FastAPI / Python on port 8002)
- **Toxiproxy** (`express-payment-proxy` on port 18003)
