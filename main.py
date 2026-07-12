import os
import json
import stripe
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
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY) if SUPABASE_URL and SUPABASE_KEY else None

STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY")
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET")
STRIPE_PAYMENT_LINK = os.environ.get("STRIPE_PAYMENT_LINK", "https://buy.stripe.com/test_placeholder")

if STRIPE_SECRET_KEY:
    stripe.api_key = STRIPE_SECRET_KEY

FREE_LIMIT = 5
PRO_LIMIT = 200

# ==========================================
# AUTH DEPENDENCY
# ==========================================
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

# ==========================================
# PAYLOAD MODEL
# ==========================================
class JobPayload(BaseModel):
    job_text: str
    user_skills: str = "Experienced Python developer skilled in FastAPI, web scraping, and AI integrations."
    model_config = ConfigDict(extra='ignore')

# ==========================================
# DRAFT GENERATION ROUTE
# ==========================================
@app.post("/draft")
async def create_draft(payload: JobPayload, current_user = Depends(get_current_user)):
    user_id = current_user.id
    
    # 1. Fetch user record
    response = supabase.table("users").select("*").eq("user_id", user_id).execute()
    user_data = response.data
    
    if not user_data:
        supabase.table("users").insert({"user_id": user_id, "generations_count": 0, "is_pro": False}).execute()
        count, is_pro = 0, False
    else:
        count, is_pro = user_data[0]["generations_count"], user_data[0]["is_pro"]

    # 2. Enforce limits
    if not is_pro and count >= FREE_LIMIT:
        return {"proposal": f"Free trial limit reached. Upgrade here: {STRIPE_PAYMENT_LINK}?client_reference_id={user_id}"}

    # 3. Generate proposal via Groq
    try:
        chat_completion = client.chat.completions.create(
            messages=[
                {"role": "system", "content": "You are an expert freelance proposal writer. Output ONLY the proposal. Under 130 words."},
                {"role": "user", "content": f"Skills: {payload.user_skills}\nJob: {payload.job_text}"}
            ],
            model="llama-3.3-70b-versatile",
            temperature=0.6,
            max_tokens=300,
        )
        proposal = chat_completion.choices[0].message.content.strip()
        
        # Increment count
        supabase.table("users").update({"generations_count": count + 1}).eq("user_id", user_id).execute()
        
        return {"proposal": proposal}
    except Exception as e:
        print(f"❌ Groq Error: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

# ==========================================
# STRIPE WEBHOOK
# ==========================================
@app.post("/stripe-webhook")
async def stripe_webhook(request: Request):
    payload = await request.body()
    # ... (Keep your existing webhook logic) ...
    return {"status": "success"}