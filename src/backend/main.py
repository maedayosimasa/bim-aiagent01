from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from .engine.spatial import analyze_space
from .database.db import create_tables
from .database.db import insert_element

# FastAPIアプリ作成
app = FastAPI()
create_tables()

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

@app.post("/bim/import_test")
def import_test():


    insert_element(

        "wall001",

        "Wall",

        "外壁",

        '{"width":200,"height":3000}',

        '{"x":0,"y":0}'

    )


    insert_element(

        "door001",

        "Door",

        "玄関扉",

        '{"width":900,"height":2100}',

        '{"x":500,"y":0}'

    )


    return {

        "status":"BIM data imported"

    }