"""HEZO Wiki (P2) 람다 핸들러 — 순수 로직(collect 등)을 호출만 하는 얇은 어댑터.

로직(search/fetch/collect)은 런타임 무관 순수 함수로 두고, 여기 핸들러는
람다 이벤트를 그 로직 호출로 번역만 한다. (ECS entrypoint도 같은 로직을 호출)
"""
