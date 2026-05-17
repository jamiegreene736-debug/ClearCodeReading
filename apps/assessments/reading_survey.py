KPI_LABELS = {
    "phonemicAwareness": "Phonemic awareness",
    "letterSound": "Letter sounds",
    "phonics": "Phonics / decoding",
    "advancedPhonics": "Advanced decoding",
    "sightWords": "Sight words",
    "fluency": "Fluency",
    "vocabulary": "Vocabulary",
    "comprehension": "Comprehension",
    "writingReadiness": "Writing readiness",
    "confidence": "Reading confidence",
}

QUESTION_SCORES = {
    "phonemicAwareness": [1, 0, 0, 0],
    "letterSound": [1, 0, 0, 0],
    "phonics": [1, 0.75, 0.35, 0],
    "advancedPhonics": [1, 0.75, 0.25, 0],
    "sightWords": [0.1, 0.35, 0.7, 1],
    "fluency": [1, 0.75, 0.35, 0],
    "vocabulary": [1, 0, 0, 0],
    "comprehension": [1, 0, 0, 0],
    "writingReadiness": [1, 0.7, 0.3, 0],
    "confidence": [1, 0.7, 0.3, 0],
}


def score_reading_survey(answer_indexes, child_age=None):
    kpi_scores = {}
    total = 0
    for kpi, scores in QUESTION_SCORES.items():
        answer_index = answer_indexes.get(kpi)
        score = scores[answer_index] if isinstance(answer_index, int) and 0 <= answer_index < len(scores) else 0
        kpi_scores[kpi] = score
        total += score

    overall = total / len(QUESTION_SCORES)
    reading_age = round(max(4, min(11, 4 + overall * 7)), 1)
    age_gap = round(reading_age - child_age, 1) if child_age else None
    strengths = [KPI_LABELS[kpi] for kpi, score in kpi_scores.items() if score >= 0.75]
    growth_areas = [KPI_LABELS[kpi] for kpi, score in kpi_scores.items() if score < 0.55]

    return {
        "overall": round(overall, 3),
        "overall_percent": round(overall * 100),
        "reading_age": reading_age,
        "age_gap": age_gap,
        "kpis": kpi_scores,
        "strengths": strengths,
        "growth_areas": growth_areas,
        "recommendation": growth_areas[0] if growth_areas else "Continue building fluency and comprehension with just-right texts",
    }
