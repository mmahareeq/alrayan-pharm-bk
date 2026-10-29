import os
import json
import base64
import pandas as pd
from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
import anthropic

app = FastAPI(title="Al-Rayan Pharmacy Backend")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

FILE = os.path.join(os.path.dirname(__file__), "skincare_products_v12.xlsx")

client = anthropic.Anthropic(api_key="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")

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

SYSTEM_PROMPT = """أنت مستشار صيدلاني خبير للبشرة في صيدلية الريان وسوبر فارم. ستستلم قائمة بالمنتجات المتوفرة، صورة بشرة الزبون، ووصف المشكلة.

القواعد الأساسية:
1. اعتمد بشكل أساسي ومطلق على الفحص البصري للصورة لتحديد نوع البشرة (مثل: دهنية، جافة، مختلطة، حساسة، عادية).
2. يجب اختيار 3 منتجات أساسية على الأقل لتغطية الروتين اليومي: (1. غسول، 2. مرطب، 3. واقي شمس).
3. حاول بأقصى درجة أن تكون المنتجات الثلاثة من **نفس الشركة/الماركة** لتكوين روتين متكامل من نفس العلامة التجارية.
4. يجب عليك استخدام أداة respond_skin_analysis فقط لإعادة النتيجة بصيغة هيكلية دقيقة."""

def load_excel_data():
    if not os.path.exists(FILE):
        import glob
        excel_files = glob.glob(os.path.join(os.path.dirname(__file__), "*.xlsx"))
        if excel_files:
            return pd.read_excel(excel_files[0]).fillna("")
        return pd.DataFrame()
    return pd.read_excel(FILE).fillna("")

@app.get("/", response_class=HTMLResponse)
def serve_frontend():
    base_dir = os.path.dirname(os.path.abspath(__file__))
    html_path = os.path.join(base_dir, "index.html")
    
    if os.path.exists(html_path):
        with open(html_path, "r", encoding="utf-8") as f:
            return f.read()
            
    return f"<h1>عذراً يا دكتور، ملف index.html غير موجود في المسار: {base_dir}</h1>"

@app.get("/products")
def get_products():
    df = load_excel_data()
    if df.empty:
        return []
    return df.to_dict(orient="records")

@app.post("/consult")
async def consult_ai(symptom: str = Form(...), file: UploadFile = File(None)):
    df = load_excel_data()
    if df.empty:
        raise HTTPException(status_code=400, detail="ملف المنتجات غير موجود في المجلد")
    
    if file is None:
        raise HTTPException(status_code=400, detail="صورة البشرة إجبارية للتحليل")

    catalog = df.to_csv(index=True, index_label="id")
    user_content = []
    
    try:
        image_bytes = await file.read()
        media_type = file.content_type if file.content_type else "image/jpeg"
        base64_image = base64.b64encode(image_bytes).decode("utf-8")
        user_content.append({
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": media_type,
                "data": base64_image,
            },
        })
    except Exception as img_err:
        raise HTTPException(status_code=400, detail=f"خطأ في قراءة الصورة: {str(img_err)}")
    
    user_content.append({
        "type": "text",
        "text": f"جدول المنتجات المتوفرة في المخزون:\n{catalog}\n\nشكوى ووصف الزبون: {symptom}"
    })

    try:
        msg = client.messages.create(
            model="claude-haiku-5-5",
            max_tokens=2500,
            system=SYSTEM_PROMPT,
            tools=[RESPONSE_TOOL],
            tool_choice={"type": "tool", "name": "respond_skin_analysis"},
            messages=[{
                "role": "user",
                "content": user_content,
            }],
        )
        
        result_json = None
        for block in msg.content:
            if block.type == "tool_use":
                result_json = block.input
                break
                
        if not result_json:
            raise HTTPException(status_code=500, detail="تعذر استخراج تحليل الذكاء الاصطناعي")

        return {
            "result": result_json,
            "products": df.to_dict(orient="index")
        }
        
    except Exception as e:
        print(f"Server Error Details: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))
