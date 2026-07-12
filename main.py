import os
import json
import stripe
from fastapi import FastAPI, Request, HTTPException, Header
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

# Initialize AI Client
client = Groq(api_key=os.environ.get("GROQ_API_KEY"))

# Initialize Supabase
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY) if SUPABASE_URL and SUPABASE_KEY else None

# Initialize Stripe
STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY")
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET")
STRIPE_PAYMENT_LINK = os.environ.get("STRIPE_PAYMENT_LINK", "https://buy.stripe.com/test_placeholder")

if STRIPE_SECRET_KEY:
    stripe.api_key = STRIPE_SECRET_KEY

class JobPayload(BaseModel):
    user_id: str
    job_text: str
    user_skills: str = "Experienced Python developer skilled in FastAPI, web scraping, and AI integrations."

FREE_LIMIT = 5

@app.post("/draft")
async def create_draft(payload: JobPayload):
    user_id = payload.user_id

    # 1. Check or register user in Supabase
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

        # 2. Enforce free usage limit
        if not is_pro and count >= FREE_LIMIT:
            user_stripe_url = f"{STRIPE_PAYMENT_LINK}?client_reference_id={user_id}"
            return {
                "proposal": (
                    f"⚠️ Free Trial Limit Reached ({FREE_LIMIT}/{FREE_LIMIT} proposals used).\n\n"
                    f"To unlock unlimited AI proposal generation, upgrade to Pro for $5/month:\n\n"
                    f"👉 Upgrade Here: {user_stripe_url}"
                )
            }

    # 3. Generate proposal via Groq LLaMA 3.3
    system_prompt = (
        "You are an expert freelance proposal writer. Your task is to output ONLY the proposal text itself. "
        "DO NOT include conversational introductory phrases like 'Here is a proposal', 'Sure!', or meta explanations. "
        "DO NOT include contact information placeholders like [Your Email] or [Your Phone Number]. "
        "Start directly with the first sentence or greeting of the proposal. Keep it under 130 words, punchy, and professional."
    )
    
    user_prompt = f"""
    Freelancer Skills & Bio:
    {payload.user_skills}
    
    Job Description:
    {payload.job_text}
    """
    
    try:
        chat_completion = client.chat.completions.create(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            model="llama-3.3-70b-versatile",
            temperature=0.6,
            max_tokens=300,
        )
        
        proposal = chat_completion.choices[0].message.content.strip()

        # 4. Increment usage count in Supabase after successful generation
        if supabase:
            supabase.table("users").update({"generations_count": count + 1}).eq("user_id", user_id).execute()

        return {"proposal": proposal}
        
    except Exception as e:
        print("Groq API Error:", e)
        return {"proposal": f"Error calling Groq API: {str(e)}"}

# ==========================================
# STRIPE WEBHOOK: Automatic Pro Upgrade
# ==========================================
@app.post("/stripe-webhook")
async def stripe_webhook(request: Request):
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature")

    event = None

    # 1. Try verifying with Stripe signature if secret is provided
    if STRIPE_WEBHOOK_SECRET and sig_header:
        try:
            event = stripe.Webhook.construct_event(
                payload, sig_header, STRIPE_WEBHOOK_SECRET
            )
        except Exception as e:
            print(f"⚠️ Webhook signature verification failed: {e}")

    # 2. Fallback to parsing raw body payload if verification is skipped during dev testing
    if not event:
        try:
            event = json.loads(payload.decode("utf-8"))
        except Exception as e:
            print(f"❌ Failed to parse payload: {e}")
            raise HTTPException(status_code=400, detail="Invalid payload")

    # 3. Handle successful checkout session
    event_type = event.get("type") if isinstance(event, dict) else event.type
    
    if event_type == "checkout.session.completed":
        data_object = event["data"]["object"] if isinstance(event, dict) else event.data.object
        
        # Extract user_id passed in client_reference_id
        user_id = data_object.get("client_reference_id")
        
        if user_id and supabase:
            supabase.table("users").update({"is_pro": True}).eq("user_id", user_id).execute()
            print(f"✅ User {user_id} upgraded to Pro!")
        else:
            print(f"⚠️ Event received, but user_id missing in client_reference_id: {user_id}")

    return {"status": "success"}

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port)