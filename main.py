import os
import json
import stripe
from fastapi import FastAPI, Request, HTTPException, Depends, Header
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from groq import Groq
from supabase import create_client, Client

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], 
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
# AUTH DEPENDENCY: Verifies JWT Token
# ==========================================
async def get_current_user(authorization: str = Header(None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Authorization header")
    
    token = authorization.split(" ")[1]
    
    try:
        # Verify JWT token with Supabase Auth
        user_response = supabase.auth.get_user(token)
        if not user_response or not user_response.user:
            raise HTTPException(status_code=401, detail="Invalid token or expired session")
        
        return user_response.user
    except Exception as e:
        raise HTTPException(status_code=401, detail=f"Authentication failed: {str(e)}")

class JobPayload(BaseModel):
    job_text: str
    user_skills: str = "Experienced Python developer skilled in FastAPI, web scraping, and AI integrations."

@app.post("/draft")
async def create_draft(payload: JobPayload, current_user = Depends(get_current_user)):
    user_id = current_user.id  # Authenticated Supabase User ID (UUID)
    user_email = current_user.email

    # 1. Fetch or create user record
    if supabase:
        response = supabase.table("users").select("*").eq("user_id", user_id).execute()
        user_data = response.data

        if not user_data:
            supabase.table("users").insert({"user_id": user_id, "generations_count": 0, "is_pro": False}).execute()
            count = 0
            is_pro = False
        else:
            count = user_data[0]["generations_count"]
            is_pro = user_data[0]["is_pro"]

        # 2. Enforce limits based on account tier
        if not is_pro and count >= FREE_LIMIT:
            user_stripe_url = f"{STRIPE_PAYMENT_LINK}?client_reference_id={user_id}"
            return {
                "proposal": (
                    f"⚠️ Free Trial Limit Reached ({FREE_LIMIT}/{FREE_LIMIT} proposals used).\n\n"
                    f"To unlock Pro access, upgrade for $5/month:\n\n"
                    f"👉 Upgrade Here: {user_stripe_url}"
                )
            }

        if is_pro and count >= PRO_LIMIT:
            return {
                "proposal": (
                    f"⚠️ Fair Use Safety Limit Reached ({PRO_LIMIT}/{PRO_LIMIT} generations used)."
                )
            }

    # 3. Generate proposal via Groq
    system_prompt = (
        "You are an expert freelance proposal writer. Your task is to output ONLY the proposal text itself. "
        "Keep it under 130 words, punchy, and professional."
    )
    
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

        # Increment usage count
        if supabase:
            supabase.table("users").update({"generations_count": count + 1}).eq("user_id", user_id).execute()

        return {"proposal": proposal}
        
    except Exception as e:
        return {"proposal": f"Error calling Groq API: {str(e)}"}

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
        try:
            event = json.loads(payload.decode("utf-8"))
        except Exception as e:
            raise HTTPException(status_code=400, detail="Invalid payload")

    event_type = event["type"] if isinstance(event, dict) else event.type
    
    if event_type == "checkout.session.completed":
        data_object = event["data"]["object"] if isinstance(event, dict) else event.data.object
        
        if isinstance(data_object, dict):
            user_id = data_object.get("client_reference_id")
        else:
            user_id = getattr(data_object, "client_reference_id", None)
        
        if user_id and supabase:
            supabase.table("users").update({"is_pro": True}).eq("user_id", user_id).execute()
            print(f"✅ User {user_id} upgraded to Pro!")

    return {"status": "success"}