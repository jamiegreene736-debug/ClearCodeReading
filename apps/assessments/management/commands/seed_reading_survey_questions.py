from django.core.management.base import BaseCommand

from apps.assessments.models import AssessmentQuestion, QuestionOption


QUESTIONS = [
    {
        "category": AssessmentQuestion.Category.PHONEMIC_AWARENESS,
        "difficulty": AssessmentQuestion.Difficulty.PRE_READER,
        "question_type": AssessmentQuestion.QuestionType.MULTIPLE_CHOICE,
        "question_text": "Listen for the sss sound: sssun. Which word starts with that same sss sound?",
        "correct_answer": {"value": "sock"},
        "sort_order": 10,
        "options": [
            {"label": "sock", "value": "sock", "is_correct": True, "score_value": "1.00"},
            {"label": "moon", "value": "moon", "is_correct": False, "score_value": "0.00"},
            {"label": "ball", "value": "ball", "is_correct": False, "score_value": "0.00"},
            {"label": "table", "value": "table", "is_correct": False, "score_value": "0.00"},
        ],
    },
    {
        "category": AssessmentQuestion.Category.PHONEMIC_AWARENESS,
        "difficulty": AssessmentQuestion.Difficulty.EMERGING,
        "question_type": AssessmentQuestion.QuestionType.MULTIPLE_CHOICE,
        "question_text": "How many sounds do you hear in the word cat?",
        "correct_answer": {"value": "3"},
        "sort_order": 20,
        "options": [
            {"label": "2", "value": "2", "is_correct": False, "score_value": "0.00"},
            {"label": "3", "value": "3", "is_correct": True, "score_value": "1.00"},
            {"label": "4", "value": "4", "is_correct": False, "score_value": "0.00"},
        ],
    },
    {
        "category": AssessmentQuestion.Category.LETTER_SOUND,
        "difficulty": AssessmentQuestion.Difficulty.PRE_READER,
        "question_type": AssessmentQuestion.QuestionType.MULTIPLE_CHOICE,
        "question_text": "Which letter makes the /m/ sound?",
        "correct_answer": {"value": "m"},
        "sort_order": 30,
        "options": [
            {"label": "M", "value": "m", "is_correct": True, "score_value": "1.00"},
            {"label": "S", "value": "s", "is_correct": False, "score_value": "0.00"},
            {"label": "T", "value": "t", "is_correct": False, "score_value": "0.00"},
        ],
    },
    {
        "category": AssessmentQuestion.Category.LETTER_SOUND,
        "difficulty": AssessmentQuestion.Difficulty.EMERGING,
        "question_type": AssessmentQuestion.QuestionType.MULTIPLE_CHOICE,
        "question_text": "Which word starts with /b/ like ball?",
        "correct_answer": {"value": "bike"},
        "sort_order": 40,
        "options": [
            {"label": "bike", "value": "bike", "is_correct": True, "score_value": "1.00"},
            {"label": "sun", "value": "sun", "is_correct": False, "score_value": "0.00"},
            {"label": "map", "value": "map", "is_correct": False, "score_value": "0.00"},
            {"label": "fish", "value": "fish", "is_correct": False, "score_value": "0.00"},
        ],
    },
    {
        "category": AssessmentQuestion.Category.PHONICS,
        "difficulty": AssessmentQuestion.Difficulty.EARLY,
        "question_type": AssessmentQuestion.QuestionType.MULTIPLE_CHOICE,
        "question_text": "Choose the word that says ship.",
        "correct_answer": {"value": "ship"},
        "sort_order": 50,
        "options": [
            {"label": "sip", "value": "sip", "is_correct": False, "score_value": "0.00"},
            {"label": "ship", "value": "ship", "is_correct": True, "score_value": "1.00"},
            {"label": "shop", "value": "shop", "is_correct": False, "score_value": "0.00"},
        ],
    },
    {
        "category": AssessmentQuestion.Category.PHONICS,
        "difficulty": AssessmentQuestion.Difficulty.EARLY,
        "question_type": AssessmentQuestion.QuestionType.MULTIPLE_CHOICE,
        "question_text": "Which word rhymes with cake?",
        "correct_answer": {"value": "make"},
        "sort_order": 60,
        "options": [
            {"label": "make", "value": "make", "is_correct": True, "score_value": "1.00"},
            {"label": "cat", "value": "cat", "is_correct": False, "score_value": "0.00"},
            {"label": "cup", "value": "cup", "is_correct": False, "score_value": "0.00"},
        ],
    },
    {
        "category": AssessmentQuestion.Category.ADVANCED_PHONICS,
        "difficulty": AssessmentQuestion.Difficulty.DEVELOPING,
        "question_type": AssessmentQuestion.QuestionType.MULTIPLE_CHOICE,
        "question_text": "Choose the word with the same vowel sound as rain.",
        "correct_answer": {"value": "train"},
        "sort_order": 70,
        "options": [
            {"label": "train", "value": "train", "is_correct": True, "score_value": "1.00"},
            {"label": "ran", "value": "ran", "is_correct": False, "score_value": "0.00"},
            {"label": "ring", "value": "ring", "is_correct": False, "score_value": "0.00"},
        ],
    },
    {
        "category": AssessmentQuestion.Category.SIGHT_WORDS,
        "difficulty": AssessmentQuestion.Difficulty.EARLY,
        "question_type": AssessmentQuestion.QuestionType.MULTIPLE_CHOICE,
        "question_text": "Which word is the sight word because?",
        "correct_answer": {"value": "because"},
        "sort_order": 80,
        "options": [
            {"label": "before", "value": "before", "is_correct": False, "score_value": "0.00"},
            {"label": "because", "value": "because", "is_correct": True, "score_value": "1.00"},
            {"label": "become", "value": "become", "is_correct": False, "score_value": "0.00"},
        ],
    },
    {
        "category": AssessmentQuestion.Category.FLUENCY,
        "difficulty": AssessmentQuestion.Difficulty.DEVELOPING,
        "question_type": AssessmentQuestion.QuestionType.RATING_SCALE,
        "question_text": "Read this sentence aloud: The little dog ran quickly to the red ball.",
        "correct_answer": {"rubric": "Evaluator or speech scoring rates accuracy, pace, and expression."},
        "sort_order": 90,
        "options": [
            {"label": "Needs support", "value": "needs_support", "is_correct": False, "score_value": "0.00"},
            {"label": "Developing", "value": "developing", "is_correct": False, "score_value": "0.50"},
            {"label": "Fluent", "value": "fluent", "is_correct": True, "score_value": "1.00"},
        ],
    },
    {
        "category": AssessmentQuestion.Category.VOCABULARY,
        "difficulty": AssessmentQuestion.Difficulty.DEVELOPING,
        "question_type": AssessmentQuestion.QuestionType.MULTIPLE_CHOICE,
        "question_text": "What does tiny mean?",
        "correct_answer": {"value": "very_small"},
        "sort_order": 100,
        "options": [
            {"label": "very small", "value": "very_small", "is_correct": True, "score_value": "1.00"},
            {"label": "very loud", "value": "very_loud", "is_correct": False, "score_value": "0.00"},
            {"label": "very fast", "value": "very_fast", "is_correct": False, "score_value": "0.00"},
        ],
    },
    {
        "category": AssessmentQuestion.Category.COMPREHENSION,
        "difficulty": AssessmentQuestion.Difficulty.DEVELOPING,
        "question_type": AssessmentQuestion.QuestionType.MULTIPLE_CHOICE,
        "question_text": "Mia packed an umbrella because the sky was dark. What will probably happen?",
        "correct_answer": {"value": "rain"},
        "sort_order": 110,
        "options": [
            {"label": "It may rain.", "value": "rain", "is_correct": True, "score_value": "1.00"},
            {"label": "It will snow.", "value": "snow", "is_correct": False, "score_value": "0.00"},
            {"label": "Mia will swim.", "value": "swim", "is_correct": False, "score_value": "0.00"},
        ],
    },
    {
        "category": AssessmentQuestion.Category.COMPREHENSION,
        "difficulty": AssessmentQuestion.Difficulty.FLUENT,
        "question_type": AssessmentQuestion.QuestionType.MULTIPLE_CHOICE,
        "question_text": "After reading a story, what should a strong retell include?",
        "correct_answer": {"value": "characters_setting_events"},
        "sort_order": 120,
        "options": [
            {"label": "Only the title", "value": "title_only", "is_correct": False, "score_value": "0.00"},
            {"label": "Characters, setting, and important events", "value": "characters_setting_events", "is_correct": True, "score_value": "1.00"},
            {"label": "Only the last word", "value": "last_word", "is_correct": False, "score_value": "0.00"},
        ],
    },
    {
        "category": AssessmentQuestion.Category.WRITING_READINESS,
        "difficulty": AssessmentQuestion.Difficulty.EMERGING,
        "question_type": AssessmentQuestion.QuestionType.FREE_RESPONSE,
        "question_text": "Write or say one sentence about your favorite animal.",
        "correct_answer": {"rubric": "Credit is awarded for a complete thought with a named animal."},
        "sort_order": 130,
        "options": [
            {"label": "Needs support", "value": "needs_support", "is_correct": False, "score_value": "0.00"},
            {"label": "Complete thought", "value": "complete_thought", "is_correct": True, "score_value": "1.00"},
        ],
    },
    {
        "category": AssessmentQuestion.Category.CONFIDENCE,
        "difficulty": AssessmentQuestion.Difficulty.PRE_READER,
        "question_type": AssessmentQuestion.QuestionType.RATING_SCALE,
        "question_text": "How do you feel when you read a new book?",
        "correct_answer": {"rubric": "Confidence item supports placement and coaching, not correctness alone."},
        "sort_order": 140,
        "options": [
            {"label": "I need lots of help", "value": "needs_help", "is_correct": False, "score_value": "0.25"},
            {"label": "I can try with some help", "value": "some_help", "is_correct": False, "score_value": "0.60"},
            {"label": "I feel ready to try", "value": "ready", "is_correct": True, "score_value": "1.00"},
        ],
    },
]


class Command(BaseCommand):
    help = "Seed starter Reading Survey questions for Clear Code Reading."

    def handle(self, *args, **options):
        created_questions = 0
        updated_questions = 0
        created_options = 0
        updated_options = 0
        active_question_texts = {question["question_text"] for question in QUESTIONS}

        for question_data in QUESTIONS:
            option_data = question_data["options"]
            question, created = AssessmentQuestion.objects.update_or_create(
                category=question_data["category"],
                question_text=question_data["question_text"],
                defaults={
                    "difficulty": question_data["difficulty"],
                    "question_type": question_data["question_type"],
                    "correct_answer": question_data["correct_answer"],
                    "options": [
                        {
                            "label": option["label"],
                            "value": option["value"],
                            "score_value": option["score_value"],
                        }
                        for option in option_data
                    ],
                    "sort_order": question_data["sort_order"],
                    "is_active": True,
                    "is_deleted": False,
                    "metadata": {"seed_source": "seed_reading_survey_questions"},
                },
            )
            created_questions += int(created)
            updated_questions += int(not created)

            active_labels = [option["label"] for option in option_data]
            QuestionOption.objects.filter(question=question).exclude(label__in=active_labels).update(is_deleted=True)

            for index, option in enumerate(option_data, start=1):
                _, option_created = QuestionOption.objects.update_or_create(
                    question=question,
                    label=option["label"],
                    defaults={
                        "value": option["value"],
                        "is_correct": option["is_correct"],
                        "score_value": option["score_value"],
                        "sort_order": index,
                        "is_deleted": False,
                        "metadata": {"seed_source": "seed_reading_survey_questions"},
                    },
                )
                created_options += int(option_created)
                updated_options += int(not option_created)

        AssessmentQuestion.objects.filter(
            metadata__seed_source="seed_reading_survey_questions",
            is_deleted=False,
        ).exclude(question_text__in=active_question_texts).update(is_deleted=True)

        self.stdout.write(
            self.style.SUCCESS(
                "Seeded Reading Survey question bank: "
                f"{created_questions} created, {updated_questions} updated; "
                f"{created_options} options created, {updated_options} options updated."
            )
        )
