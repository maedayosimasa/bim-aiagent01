from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from .engine.spatial import analyze_space

# FastAPIアプリ作成
app = FastAPI()


# ==============================
# CORS設定
# React(Vite)からの通信を許可
# ==============================

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173"
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ==============================
# Requestモデル
# Reactから送信されるJSON形式
# ==============================

class AnalyzeRequest(BaseModel):
    model_id: str



# ==============================
# Root API
# ==============================

@app.get("/")
def root():
    return {
        "message": "FastAPI OK"
    }



# ==============================
# Health Check API
# ==============================

@app.get("/health")
def health():
    return {
        "status": "ok"
    }



@app.post("/analyze")
def analyze(data: AnalyzeRequest):

    result = analyze_space(
        data.model_id
    )

    return result