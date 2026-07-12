import os
import json
import stripe
import uvicorn
from fastapi import FastAPI, Request, HTTPException, Depends, Header
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ConfigDict
from groq import Groq
from supabase import create_client, Client

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://www.upwork.com"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

client = Groq(api_key=os.environ.get("GROQ_API_KEY"))

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
if not SUPABASE_URL or not SUPABASE_KEY:
    raise ValueError("SUPABASE_URL and SUPABASE_KEY must be set!")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY")
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET")
STRIPE_PAYMENT_LINK = os.environ.get("STRIPE_PAYMENT_LINK", "https://buy.stripe.com/test_placeholder")

if STRIPE_SECRET_KEY:
    stripe.api_key = STRIPE_SECRET_KEY

FREE_LIMIT = 5
PRO_LIMIT = 200

async def get_current_user(authorization: str = Header(None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")
    token = authorization.split(" ")[1]
    try:
        user_response = supabase.auth.get_user(jwt=token)
        if not user_response or not user_response.user:
            raise HTTPException(status_code=401, detail="Invalid token")
        return user_response.user
    except Exception as e:
        raise HTTPException(status_code=401, detail=f"Auth failed: {str(e)}")

class JobPayload(BaseModel):
    job_text: str
    user_skills: str = "Experienced Python developer skilled in FastAPI, web scraping, and AI integrations."
    model_config = ConfigDict(extra='ignore')

@app.post("/draft")
async def create_draft(payload: JobPayload, current_user = Depends(get_current_user)):
    user_id = current_user.id
    response = supabase.table("users").select("*").eq("user_id", user_id).execute()
    user_data = response.data

    if not user_data:
        supabase.table("users").insert({"user_id": user_id, "generations_count": 0, "is_pro": False}).execute()
        count, is_pro = 0, False
    else:
        count, is_pro = user_data[0]["generations_count"], user_data[0]["is_pro"]

    if not is_pro and count >= FREE_LIMIT:
        return {"proposal": f"Free trial limit reached. Upgrade here: {STRIPE_PAYMENT_LINK}?client_reference_id={user_id}"}

    system_prompt = "You are an expert freelance proposal writer. Output ONLY the proposal. Keep it under 130 words, punchy, and professional."
    
    try:
        chat_completion = client.chat.completions.create(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": f"Skills: {payload.user_skills}\nJob: {payload.job_text}"}
            ],
            model="llama-3.3-70b-versatile",
            temperature=0.6,
            max_tokens=300,
        )
        proposal = chat_completion.choices[0].message.content.strip()
        supabase.table("users").update({"generations_count": count + 1}).eq("user_id", user_id).execute()
        return {"proposal": proposal}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/stripe-webhook")
async def stripe_webhook(request: Request):
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature")
    event = None
    if STRIPE_WEBHOOK_SECRET and sig_header:
        try:
            event = stripe.Webhook.construct_event(payload, sig_header, STRIPE_WEBHOOK_SECRET)
        except Exception as e:
            print(f"⚠️ Webhook signature failed: {e}")
    if not event:
        event = json.loads(payload.decode("utf-8"))
    
    event_type = event["type"] if isinstance(event, dict) else event.type
    if event_type == "checkout.session.completed":
        data_object = event["data"]["object"] if isinstance(event, dict) else event.data.object
        user_id = data_object.get("client_reference_id") if isinstance(data_object, dict) else getattr(data_object, "client_reference_id", None)
        if user_id:
            supabase.table("users").update({"is_pro": True}).eq("user_id", user_id).execute()
    return {"status": "success"}

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))
    uvicorn.run("main:app", host="0.0.0.0", port=port)