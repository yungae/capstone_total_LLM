from flask import Flask, request, jsonify
from config import *
from model.qa_chain import get_chain
from model.similarity_check import check_replace
from nutrition_ai import get_nutrition_info
import re

app = Flask(__name__)
qa_chain = get_chain()

#################################### 레시피 출력 LLM ###########################
"""
    입력 (예시)
    "question" : "대파, 마늘, 소안심 , 간단한 아침 식사로 먹기 좋을 요리로 부탁해"
    출력 (예시)
    {
     "description": "국간장 대신 진간장을 사용하여 더욱 깊고 풍부한 맛을 내는 돼지불고기 레시피입니다.",
     "ingredients": [ {"amount": "(불고기용) 600g", "name": "돼지고기"}, {"amount": "1개", "name": "양파"}, { ... }, { ... }],
     "instructions" : [ {"description": "돼지고기 준비: 불고기용 돼지고기를 키친타월로 꾹꾹 눌러 핏물을 제거합니다. 이렇게 하면 잡내를 줄이고 양념이 더 잘 배어들게 됩니다.", "step": 1},
                        {"description": "채소 준비: 양파는 얇게 채 썰고, 대파는 어슷하게 썰어줍니다. 당근을 사용한다면 얇게 채 썰어 준비합니다. 마늘과 생강은 곱게 다져줍니다.", "step": 2},
                         {...} ]
     "name": "진간장 돼지불고기",
     "user": null
    }
"""
@app.route('/ask', methods=['POST'])
def ask():
    data = request.get_json()
    query = data.get("question")

    if not query:
        return jsonify({"error": "질문이 비어 있습니다"}), 400

    result = qa_chain.invoke({"question": query})
    text = result["answer"]

    response = {
        "name": extract_name(text),
        "description": extract_description(text),
        "ingredients": extract_ingredients(text),
        "instructions": extract_instructions(text),
        "user": {
            "id": 1,
            "name": "신짱구"
        }
    }

    return jsonify(response)

# 레시피 이름 추출
def extract_name(text):
    match = re.search(r"- name\s*:\s*(.+)", text)
    return match.group(1).strip() if match else "이름 없음"

# 설명 추출
def extract_description(text):
    match = re.search(r"- description\s*:\s*(.+)", text)
    return match.group(1).strip() if match else "설명 없음"

# 재료 리스트 추출
def extract_ingredients(text):
    ingredients = []
    match = re.search(r"- ingredients\s*:\s*((?:\n\s*\*.+)+)", text)
    if match:
        raw = match.group(1).strip().split('\n')
        for line in raw:
            item = re.sub(r"^\*\s*", "", line).strip()
            if item:
                parts = item.split(' ', 1)
                if len(parts) == 2:
                    name, amount = parts
                else:
                    name, amount = parts[0], ""
                ingredients.append({"name": name.strip(), "amount": amount.strip()})
    return ingredients

# 조리 단계 추출
def extract_instructions(text):
    instructions = []
    matches = re.findall(r"###\s*(\d+)단계\s*###\n(.+?)(?=\n###|\Z)", text, re.DOTALL)
    for step, desc in matches:
        instructions.append({
            "step": int(step),
            "text": desc.strip()
        })
    return instructions

####################### 대체재료 기반 LLM ###############################

"""
    입력 (예시)
    {
    "ori": "국간장",
    "sub": "진간장",
    "recipe": "간장돼지불고기"
    }
    출력 (예시)
    {
     "description": "국간장 대신 진간장을 사용하여 더욱 깊고 풍부한 맛을 내는 돼지불고기 레시피입니다.",
     "ingredients": [ {"amount": "(불고기용) 600g", "name": "돼지고기"}, {"amount": "1개", "name": "양파"}, { ... }, { ... }],
     "instructions" : [ {"description": "돼지고기 준비: 불고기용 돼지고기를 키친타월로 꾹꾹 눌러 핏물을 제거합니다. 이렇게 하면 잡내를 줄이고 양념이 더 잘 배어들게 됩니다.", "step": 1},
                        {"description": "채소 준비: 양파는 얇게 채 썰고, 대파는 어슷하게 썰어줍니다. 당근을 사용한다면 얇게 채 썰어 준비합니다. 마늘과 생강은 곱게 다져줍니다.", "step": 2},
                         {...} ]
     "name": "진간장 돼지불고기",
     "user": null
    }
"""

@app.route('/generate_recipe_or_reject', methods=['POST'])
def generate_recipe_or_reject():
    data = request.get_json()
    ori = data.get("ori")
    sub = data.get("sub")
    recipe = data.get("recipe")

    if not all([ori, sub, recipe]):
        return jsonify({"error": "요청 필드가 부족합니다."}), 400

    try:
        similarity_score = check_replace(ori, sub)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    if similarity_score < 0.2:
        return jsonify({
            "name": recipe,
            "description": f"{ori}를 {sub}로 대체하는 것은 적절하지 않아 레시피를 생성할 수 없습니다.",
            "ingredients": [],
            "instructions": [],
            "user": None
        })

    # LLM 질의 및 응답
    query = f"{ori}를 {sub}로 교체한 {recipe}의 레시피를 알려줘"
    result = qa_chain.invoke({"question": query})
    raw = result["answer"]

    # 🔍 파싱 시작
    try:
        name = re.search(r'- name *: *(.*)', raw).group(1).strip()
        description = re.search(r'- description *: *(.*)', raw).group(1).strip()

        # ingredients 파싱
        ingredients_raw = re.findall(r'\* *(.*)', raw)
        ingredients = []
        for item in ingredients_raw:
            parts = item.split(' ', 1)
            if len(parts) == 2:
                ingredients.append({"name": parts[0], "amount": parts[1]})
            else:
                ingredients.append({"name": parts[0], "amount": ""})

        # instructions 파싱
        instructions_raw = re.findall(r'### *\d+단계 *###\n(.+?)(?=\n###|\Z)', raw, re.DOTALL)
        instructions = [
            {"step": idx + 1, "description": step.strip()}
            for idx, step in enumerate(instructions_raw)
        ]

        response_json = {
            "name": name,
            "description": description,
            "ingredients": ingredients,
            "instructions": instructions,
            "user": None
        }

        return jsonify(response_json)

    except Exception as e:
        return jsonify({"error": f"레시피 파싱 중 오류 발생: {str(e)}"}), 500


######################## 영양소 출력 LLM #####################################
"""
    입력 (예시)
    {
    "ingredients" : "소안심200g, 대파 1대, 마늘 5쪽, 간장 1큰술, 굴소스 1/2큰술, 참기름 1/2큰술, 후추 약간, 식용유 적당량, 소고기 대파 마늘볶음"
    }
    출력 (예시)
    {
    "calories": 500.0,
    "carbohydrate": 12.5,
    "cholesterol": 0.0,
    "fat": 30.0,
    "protein": 45.0,
    "saturatedFat": 0.0,
    "sodium": 600.0,
    "sugar": 6.5,
    "transFat": 0.0
}
"""

@app.route("/nutrition", methods=["POST"])
def nutrition():
    try:
        data = request.get_json()
        ingredients = data.get("ingredients")

        if not ingredients:
            return jsonify({"error": "No 'ingredients' field in request"}), 400

        response_text = get_nutrition_info(ingredients)
        print("🧠 모델 응답:\n", response_text)
        if not response_text:
            return jsonify({"error": "모델 응답이 비었습니다."}), 500

        result = extract_nutrition(response_text)
        return jsonify(result)

    except Exception as e:
        return jsonify({"error": str(e)}), 500


def extract_nutrition(text):
    def extract_value(pattern, default=0.0):
        match = re.search(pattern, text)
        if match:
            value = match.group(1)
            if '-' in value or '~' in value:
                parts = re.split(r"[-~]", value)
                nums = [float(p.strip()) for p in parts if p.strip().replace('.', '', 1).isdigit()]
                return sum(nums) / len(nums) if nums else default
            value = re.sub(r"[^\d.]", "", value)
            return float(value) if value else default
        return default

    return {
        "calories": extract_value(r"칼로리\s*:\s*약\s*([\d\-~]+)kcal"),     # kcal
        "carbohydrate": extract_value(r"탄수화물\s*:\s*약\s*([\d\-~]+)g"),  # g  
        "protein": extract_value(r"단백질\s*:\s*약\s*([\d\-~]+)g"),         # g
        "fat": extract_value(r"지방\s*:\s*약\s*([\d\-~]+)g"),               # g
        "sugar": extract_value(r"당\s*:\s*약\s*([\d\-~]+)g"),               # g
        "sodium": extract_value(r"나트륨\s*:\s*약\s*([\d\-~]+)mg"),         # mg
        "saturatedFat": extract_value(r"포화지방\s*:\s*([\d\-~]+)g"),       # g
        "transFat": extract_value(r"트랜스지방\s*:\s*([\d.]+)g"),           # g
        "cholesterol": extract_value(r"콜레스테롤\s*:\s*([\d\-~]+)mg")      # mg
    }

if __name__ == '__main__':
    app.run(debug=True)