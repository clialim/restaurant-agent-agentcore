"""식당 추천 에이전트 — AgentCore Runtime 배포용"""
from bedrock_agentcore import BedrockAgentCoreApp
from strands import Agent, tool
from strands.models import BedrockModel


# --- 도구 정의 ---
@tool
def search_restaurants(location: str, cuisine: str, budget: int) -> str:
    """위치, 요리 종류, 예산으로 식당을 검색합니다."""
    restaurants = {
        "이탈리안": "트라토리아 벨라 (강남역 3분, 1인 45,000원, 평점 4.5)",
        "한식": "한우명가 (역삼역 5분, 1인 65,000원, 평점 4.3)",
        "프렌치": "르 비스트로 (압구정역 2분, 1인 90,000원, 평점 4.6)",
    }
    result = restaurants.get(cuisine, f"{location}에서 {cuisine} 식당을 찾지 못했습니다.")
    return f"[검색 결과] {result}"

@tool
def get_restaurant_reviews(restaurant_name: str) -> str:
    """식당 리뷰를 조회합니다."""
    return f"[리뷰] {restaurant_name}: '분위기 훌륭하고 서비스 최고' (★4.5, 리뷰 128건)"

# --- 에이전트 생성 ---
agent = Agent(
    model=BedrockModel(model_id="us.anthropic.claude-sonnet-4-6"),
    system_prompt="""당신은 강남 지역 식당 추천 전문가입니다.
    - 위치, 분위기, 예산을 고려하여 추천
    - 존댓말 필수
    - 확인되지 않은 정보 추측 금지""",
    tools=[search_restaurants, get_restaurant_reviews],
    callback_handler=None,  # 스트리밍 중간 출력 억제 — 최종 print만 표시
)

# --- AgentCore Runtime 래핑 ---
app = BedrockAgentCoreApp()

@app.entrypoint
def handler(payload):
    """AgentCore Runtime 엔트리포인트"""
    result = agent(payload.get("prompt"))
    return {"result": str(result)}

if __name__ == "__main__":
    app.run()