import asyncio
from app.ai.rerankers.gravity_reranker import GravityReranker

reranker = GravityReranker()
content = "SECTION_PATH: 0 Introducción > 0.1 Generalidades\n\nEste es el contenido."
score = reranker._heading_boost("que dice la introduccion del documento?", content)
print(f"Test 1 score: {score}")

content2 = "SECTION_PATH: 0 Int > 0.1 Gen\n\nEste es el contenido."
score2 = reranker._heading_boost("que dice la introduccion del documento?", content2)
print(f"Test 2 score: {score2}")
