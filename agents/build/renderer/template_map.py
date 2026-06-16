"""
P1 챗봇 시맨틱 template_id → 실제 파일명 매핑

P1은 "landing_tax", "store_cafe" 같은 시맨틱 이름을 사용.
실제 파일명은 "13-tax-accounting.html" 형태.
두 형태 모두 지원.
"""

# { category: { semantic_id: filename_without_ext } }
TEMPLATE_MAP: dict[str, dict[str, str]] = {
    "landing": {
        # 숫자 형태 (직접 매핑)
        "01-clinic-landing":    "01-clinic-landing",
        "02-course-landing":    "02-course-landing",
        "03-saas-product":      "03-saas-product",
        "04-long-term-rental":  "04-long-term-rental",
        "05-lifting-clinic":    "05-lifting-clinic",
        "06-hanok-stay":        "06-hanok-stay",
        "07-legal-consulting":  "07-legal-consulting",
        "08-creator-agency":    "08-creator-agency",
        "09-fitness-studio":    "09-fitness-studio",
        "10-wedding-studio":    "10-wedding-studio",
        "11-language-academy":  "11-language-academy",
        "12-interior-remodel":  "12-interior-remodel",
        "13-tax-accounting":    "13-tax-accounting",
        "14-mind-counseling":   "14-mind-counseling",
        "15-live-event":        "15-live-event",
        "16-franchise-startup": "16-franchise-startup",
        "17-solar-energy":      "17-solar-energy",
        "18-mobile-app-launch": "18-mobile-app-launch",
        "19-pet-hospital":      "19-pet-hospital",
        "20-restaurant-franchise": "20-restaurant-franchise",
        # 시맨틱 형태 (P1 챗봇 출력)
        "landing_clinic":       "01-clinic-landing",
        "landing_dental":       "01-clinic-landing",
        "landing_medical":      "01-clinic-landing",
        "landing_course":       "02-course-landing",
        "landing_bootcamp":     "02-course-landing",
        "landing_edu":          "02-course-landing",
        "landing_saas":         "03-saas-product",
        "landing_software":     "03-saas-product",
        "landing_rental":       "04-long-term-rental",
        "landing_realty":       "04-long-term-rental",
        "landing_lifting":      "05-lifting-clinic",
        "landing_skin":         "05-lifting-clinic",
        "landing_beauty":       "05-lifting-clinic",
        "landing_hanok":        "06-hanok-stay",
        "landing_stay":         "06-hanok-stay",
        "landing_legal":        "07-legal-consulting",
        "landing_law":          "07-legal-consulting",
        "landing_creator":      "08-creator-agency",
        "landing_agency":       "08-creator-agency",
        "landing_fitness":      "09-fitness-studio",
        "landing_gym":          "09-fitness-studio",
        "landing_wedding":      "10-wedding-studio",
        "landing_studio":       "10-wedding-studio",
        "landing_language":     "11-language-academy",
        "landing_academy":      "11-language-academy",
        "landing_interior":     "12-interior-remodel",
        "landing_remodel":      "12-interior-remodel",
        "landing_tax":          "13-tax-accounting",
        "landing_accounting":   "13-tax-accounting",
        "landing_counsel":      "14-mind-counseling",
        "landing_therapy":      "14-mind-counseling",
        "landing_event":        "15-live-event",
        "landing_franchise":    "16-franchise-startup",
        "landing_solar":        "17-solar-energy",
        "landing_energy":       "17-solar-energy",
        "landing_app":          "18-mobile-app-launch",
        "landing_mobile":       "18-mobile-app-launch",
        "landing_pet":          "19-pet-hospital",
        "landing_vet":          "19-pet-hospital",
        "landing_restaurant":   "20-restaurant-franchise",
        "landing_food":         "20-restaurant-franchise",
        # 기타 alias
        "medical-clinic":       "01-clinic-landing",
        "consulting":           "07-legal-consulting",
        "beauty-salon":         "05-lifting-clinic",
    },
    "store": {
        "01-cafe-menu":         "01-cafe-menu",
        "02-fashion-select":    "02-fashion-select",
        "03-handmade-studio":   "03-handmade-studio",
        "04-digital-goods":     "04-digital-goods",
        "05-booking-service":   "05-booking-service",
        "06-oops-nail":         "06-oops-nail",
        "07-fruits-basket":     "07-fruits-basket",
        "08-sneaker-drop":      "08-sneaker-drop",
        "09-plant-shop":        "09-plant-shop",
        "10-wine-market":       "10-wine-market",
        "11-furniture-studio":  "11-furniture-studio",
        "12-pet-supplies":      "12-pet-supplies",
        "13-skincare-lab":      "13-skincare-lab",
        "14-grocery-market":    "14-grocery-market",
        "15-tech-gadget":       "15-tech-gadget",
        "16-book-curation":     "16-book-curation",
        "17-kids-toy":          "17-kids-toy",
        "18-outdoor-gear":      "18-outdoor-gear",
        "19-jewelry-atelier":   "19-jewelry-atelier",
        "20-tea-collection":    "20-tea-collection",
        "store_cafe":           "01-cafe-menu",
        "store_coffee":         "01-cafe-menu",
        "store_fashion":        "02-fashion-select",
        "store_clothes":        "02-fashion-select",
        "store_handmade":       "03-handmade-studio",
        "store_craft":          "03-handmade-studio",
        "store_digital":        "04-digital-goods",
        "store_booking":        "05-booking-service",
        "store_nail":           "06-oops-nail",
        "store_beauty":         "06-oops-nail",
        "store_fruits":         "07-fruits-basket",
        "store_grocery":        "14-grocery-market",
        "store_sneaker":        "08-sneaker-drop",
        "store_shoes":          "08-sneaker-drop",
        "store_plant":          "09-plant-shop",
        "store_wine":           "10-wine-market",
        "store_alcohol":        "10-wine-market",
        "store_furniture":      "11-furniture-studio",
        "store_pet":            "12-pet-supplies",
        "store_skincare":       "13-skincare-lab",
        "store_cosmetics":      "13-skincare-lab",
        "store_tech":           "15-tech-gadget",
        "store_gadget":         "15-tech-gadget",
        "store_book":           "16-book-curation",
        "store_kids":           "17-kids-toy",
        "store_toy":            "17-kids-toy",
        "store_outdoor":        "18-outdoor-gear",
        "store_camping":        "18-outdoor-gear",
        "store_jewelry":        "19-jewelry-atelier",
        "store_tea":            "20-tea-collection",
        "store_health":         "20-tea-collection",
    },
    "blog": {
        "01-food-travel-blog":  "01-food-travel-blog",
        "02-daily-life-blog":   "02-daily-life-blog",
        "03-developer-docs":    "03-developer-docs",
        "04-magazine-grid":     "04-magazine-grid",
        "05-expert-column":     "05-expert-column",
        "06-recipe-kitchen":    "06-recipe-kitchen",
        "07-travel-atlas":      "07-travel-atlas",
        "08-wellness-journal":  "08-wellness-journal",
        "09-finance-memo":      "09-finance-memo",
        "10-art-design-log":    "10-art-design-log",
        "11-parenting-community": "11-parenting-community",
        "12-newsletter-digest": "12-newsletter-digest",
        "13-beauty-review":     "13-beauty-review",
        "14-real-estate-journal": "14-real-estate-journal",
        "15-music-review":      "15-music-review",
        "16-fitness-diet-log":  "16-fitness-diet-log",
        "17-career-notebook":   "17-career-notebook",
        "18-book-essay":        "18-book-essay",
        "19-photo-diary":       "19-photo-diary",
        "20-local-food-guide":  "20-local-food-guide",
        "blog_food":            "01-food-travel-blog",
        "blog_travel":          "07-travel-atlas",
        "blog_daily":           "02-daily-life-blog",
        "blog_dev":             "03-developer-docs",
        "blog_tech":            "03-developer-docs",
        "blog_magazine":        "04-magazine-grid",
        "blog_expert":          "05-expert-column",
        "blog_recipe":          "06-recipe-kitchen",
        "blog_cook":            "06-recipe-kitchen",
        "blog_wellness":        "08-wellness-journal",
        "blog_health":          "08-wellness-journal",
        "blog_finance":         "09-finance-memo",
        "blog_money":           "09-finance-memo",
        "blog_art":             "10-art-design-log",
        "blog_design":          "10-art-design-log",
        "blog_parent":          "11-parenting-community",
        "blog_newsletter":      "12-newsletter-digest",
        "blog_beauty":          "13-beauty-review",
        "blog_realty":          "14-real-estate-journal",
        "blog_music":           "15-music-review",
        "blog_fitness":         "16-fitness-diet-log",
        "blog_career":          "17-career-notebook",
        "blog_book":            "18-book-essay",
        "blog_photo":           "19-photo-diary",
        "blog_local":           "20-local-food-guide",
        # 기존 alias
        "tech-blog":            "03-developer-docs",
        "01-study-notebook":    "01-food-travel-blog",
    },
    "multi": {  # "multi" → blog 동일 취급
        "blog_food":            "01-food-travel-blog",
        "blog_dev":             "03-developer-docs",
        "blog_tech":            "03-developer-docs",
    },
}

# 카테고리별 기본 템플릿 (매핑 실패 시 fallback)
DEFAULT_TEMPLATE: dict[str, str] = {
    "landing": "01-clinic-landing",
    "store":   "01-cafe-menu",
    "blog":    "01-food-travel-blog",
    "multi":   "01-food-travel-blog",
}


def resolve_template_filename(template_id: str, category: str) -> str:
    """
    template_id + category → 실제 파일명(확장자 제외) 반환.
    매핑 없으면 카테고리별 기본 템플릿 반환.
    """
    cat = category.lower().strip()
    cat_map = TEMPLATE_MAP.get(cat, TEMPLATE_MAP.get("landing", {}))

    # 직접 매핑
    if filename := cat_map.get(template_id):
        return filename

    # 대소문자·하이픈·언더스코어 정규화 후 재시도
    normalized = template_id.lower().replace("-", "_")
    for key, val in cat_map.items():
        if key.lower().replace("-", "_") == normalized:
            return val

    # slug 부분 매칭 (e.g. "tax" in "landing_tax" → "13-tax-accounting")
    for key, val in cat_map.items():
        if template_id in key or key in template_id:
            return val

    return DEFAULT_TEMPLATE.get(cat, "01-clinic-landing")
