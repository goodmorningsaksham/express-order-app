from fastapi import FastAPI
from pydantic import BaseModel
import time

app = FastAPI(title="Payment Service")

class AuthRequest(BaseModel):
    order_id: str
    amount: float

@app.get("/health")
def health():
    return {"status": "healthy"}

@app.get("/metrics")
def metrics():
    return ""

@app.post("/authorize")
def authorize(req: AuthRequest):
    return {
        "status": "authorized",
        "payment_id": f"pay_{req.order_id}_{int(time.time()*1000)}",
        "order_id": req.order_id,
        "amount": req.amount
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8002)
