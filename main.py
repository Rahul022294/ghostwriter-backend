import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from groq import Groq

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], 
    allow_methods=["*"],
    allow_headers=["*"],
)

client = Groq(api_key=os.environ.get("GROQ_API_KEY"))

class JobPayload(BaseModel):
    job_text: str
    user_skills: str = "Experienced Python developer skilled in FastAPI, web scraping, and AI integrations."

@app.post("/draft")
async def create_draft(payload: JobPayload):
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
        return {"proposal": proposal}
        
    except Exception as e:
        print("Groq API Error:", e)
        return {"proposal": f"Error calling Groq API: {str(e)}"}

# Crucial for Render deployment: Binds to Render's dynamic PORT environment variable
if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port)