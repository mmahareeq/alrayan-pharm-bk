import os
import json
import base64
import httpx
import pandas as pd
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse

app = FastAPI(title="Al-Rayan Pharmacy Backend")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

FILE = os.path.join(os.path.dirname(__file__), "skincare_products_v12.xlsx")
STATS_FILE = os.path.join(os.path.dirname(__file__), "usage.json")

# قراءة مفتاح الـ API وتنظيفه
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "").strip()

RESPONSE_TOOL = {
    "name": "respond_skin_analysis",
    "description": "إرسال نتيجة تحليل البشرة والروتين اليومي واختيار المنتجات",
    "input_schema": {
        "type": "object",
        "properties": {
            "skin_type": {
                "type": "string",
                "description": "نوع البشرة المترتب على الفحص البصري للصورة"
            },
            "routine_advice": {
                "type": "string",
                "description": "روتين العناية اليومي (غسول + مرطب + واقي شمس) من نفس الماركة إن أمكن"
            },
            "picks": {
                "type": "array",
                "description": "قائمة المنتجات المختارة من الجدول (3 منتجات على الأقل: غسول، مرطب، واقي شمس)",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "integer", "description": "رقم معرف الصنف من id في الجدول"},
                        "reason": {"type": "string", "description": "سبب اختيار المنتج وطريقة الاستخدام"}
                    },
                    "required": ["id", "reason"]
                }
            },
            "warning": {
                "type": "string",
                "description": "ملاحظة أو تنبيه للمستخدم إن وجد"
            },
            "see_pharmacist": {
                "type": "boolean",
                "description": "هل الحالة تستدعي زيارة صيدلاني أو طبيب"
            }
        },
        "required": ["skin_type", "routine_advice", "picks", "see_pharmacist"]
    }
}

SYSTEM_PROMPT = """أنت صيدلاني خبير في صيدلية الريان وسوبر فارم. مهمتك تحليل صورة البشرة ووصف المشكلة، ثم اقتراح روتين دقيق ومنتجات من جدول المخزون المرفق.

قواعد صارمة جداً:
1. حدد نوع البشرة بدقة من الفحص البصري للصورة ووصف الزبون.
2. اكتب روتين العناية اليومي (صباحاً ومساءً) بشكل واضح.
لكل منتج تختاره في الحقل .3 `picks`:
   - انظر حصرياً إلى صف المنتج الذي يحمل نفس الـ id في الجدول المرفق (اسم الصنف، لشو بزيط، طريقة الاستخدام).
   - اكتب في حقل `reason` (سبب الاختيار) **السبب الاستشاري الاحترافي** المناسب لحالة الزبون مستنداً إلى خصائص هذا المنتج تحديداً من جدوله الخاص، وممنوع نهائياً أخذ سبب منتج ووضعه لمنتج آخر."""
def load_excel_data():
    if not os.path.exists(FILE):
        import glob
        excel_files = glob.glob(os.path.join(os.path.dirname(__file__), "*.xlsx"))
        if excel_files:
            df = pd.read_excel(excel_files[0]).fillna("")
        else:
            return pd.DataFrame()
    else:
        df = pd.read_excel(FILE).fillna("")
    
    # التأكد من أن عمود الـ id (أو أول عمود يمثل الرقم) هو الفهرس الأساسي
    # نفترض أن عمود الـ id هو أول عمود أو اسمه 'id' أو '#'
    if '#' in df.columns:
        df.set_index('#', inplace=True)
    elif 'id' in df.columns:
        df.set_index('id', inplace=True)
        
    return df
def increment_usage():
    count = 0
    if os.path.exists(STATS_FILE):
        try:
            with open(STATS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                count = data.get("count", 0)
        except Exception:
            pass
    count += 1
    try:
        with open(STATS_FILE, "w", encoding="utf-8") as f:
            json.dump({"count": count}, f)
    except Exception:
        pass
    return count

def get_usage_count():
    if os.path.exists(STATS_FILE):
        try:
            with open(STATS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data.get("count", 0)
        except Exception:
            pass
    return 0
@app.get("/", response_class=HTMLResponse)
def serve_frontend():
    base_dir = os.path.dirname(os.path.abspath(__file__))
    html_path = os.path.join(base_dir, "index.html")
    if os.path.exists(html_path):
        with open(html_path, "r", encoding="utf-8") as f:
            return f.read()
    return "<h1>مرحباً بك في سيرفر صيدلية الريان</h1>"

@app.get("/products")
def get_products():
    df = load_excel_data()
    if df.empty:
        return []
    return df.to_dict(orient="records")

@app.post("/consult")
async def consult_ai(symptom: str = Form(...), file: UploadFile = File(None)):
    current_count = increment_usage()
    print(f"📊 Total app usages so far: {current_count}")
    df = load_excel_data()
    if df.empty:
        raise HTTPException(status_code=400, detail="ملف المنتجات غير موجود في المجلد")
    
    if file is None:
        raise HTTPException(status_code=400, detail="صورة البشرة إجبارية للتحليل")

    catalog = df.to_csv(index=True, index_label="id")
    
    try:
        image_bytes = await file.read()
        media_type = file.content_type if file.content_type else "image/jpeg"
        base64_image = base64.b64encode(image_bytes).decode("utf-8")
    except Exception as img_err:
        raise HTTPException(status_code=400, detail=f"خطأ في قراءة الصورة: {str(img_err)}")

    # بناء الطلب المباشر لـ Anthropic API عبر HTTP Headers نظامية وآمنة تماماً
    headers = {
        "x-api-key": ANTHROPIC_API_KEY,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json"
    }

    payload = {
        "model": "claude-haiku-5-5",
        "max_tokens": 20000,
        "system": SYSTEM_PROMPT,
        "tools": [RESPONSE_TOOL],
        "tool_choice": {"type": "tool", "name": "respond_skin_analysis"},
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": base64_image,
                        },
                    },
                    {
                        "type": "text",
                        "text": f"جدول المنتجات المتوفرة في المخزون:\n{catalog}\n\nشكوى ووصف الزبون: {symptom}"
                    }
                ]
            }
        ]
    }

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post("https://api.anthropic.com/v1/messages", headers=headers, json=payload)
            
        if response.status_code != 200:
            raise HTTPException(status_code=response.status_code, detail=f"Anthropic API Error: {response.text}")
            
        resp_data = response.json()
        print("--- DEBUG AI RESPONSE ---")
        print(json.dumps(resp_data, ensure_ascii=False, indent=2))
        print("-------------------------")
        
        result_json = None
        for block in resp_data.get("content", []):
            if block.get("type") == "tool_use":
                result_json = block.get("input")
                break
                
        if not result_json:
            raise HTTPException(status_code=500, detail="تعذر استخراج تحليل الذكاء الاصطناعي من الاستجابة")
       
        return {
            "result": result_json,
            "products": df.to_dict(orient="index")
        }
        
    except Exception as e:
        print(f"Server Error Details: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))
@app.get("/stats")
def get_stats():
    total = get_usage_count()
    return {
        "status": "success",
        "total_uses": total,
        "message": f"عدد مرات استخدام تطبيق صيدلية الريان حتى الآن هو: {total} مرة"
    }
